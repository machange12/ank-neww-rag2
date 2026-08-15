"""Tests for the legal-evidence domain (WP4) — pure logic, no DB.

Required cases:
  * as_of_date version lookup returns the correct version / passage
  * uncommenced / repealed cannot be current
  * generic doc cannot be primary-law / authority_tier without explicit
    source_class=public_primary + status
  * passage citations resolve to immutable version + hash + stable pinpoint
"""
from __future__ import annotations

from datetime import date

import pytest

from corpus.legal_evidence.models import LegalDocument, LegalDocumentVersion, LegalPassage, LegalSource
from corpus.legal_evidence.seed import (
    draft_regulation,
    generic_private_doc,
    land_admin_2025,
    repealed_instrument,
)
from corpus.legal_evidence.status import (
    STATUS_DRAFT,
    STATUS_IN_FORCE,
    STATUS_NOT_IN_FORCE,
    STATUS_REPEALED,
    effective_status,
    is_currently_operational,
    persistable_operational_status,
)
from corpus.legal_evidence.temporal import (
    TemporalError,
    RetrievalScope,
    expansion_inherits,
    is_primary_law,
    lock_scope,
    primary_law_authority_tier,
    resolve_passage,
    resolve_version,
    stable_pinpoint,
)


def test_as_of_date_returns_v1_before_v2():
    source, doc, versions = land_admin_2025()
    v, reason = resolve_version(doc, date(2025, 8, 1), versions)
    assert v is not None and v.version_label == "1.0"
    assert reason == "latest effective version"


def test_as_of_date_returns_v2_after_commencement():
    source, doc, versions = land_admin_2025()
    v, _ = resolve_version(doc, date(2026, 6, 15), versions)
    assert v is not None and v.version_label == "2.0"
    # the changed provision (section 4) differs between versions
    assert "annual audit" in v.passages[1].rendered_text


def test_as_of_date_before_commencement_returns_none():
    source, doc, versions = land_admin_2025()
    v, reason = resolve_version(doc, date(2025, 1, 1), versions)
    assert v is None
    assert "no version is effective" in reason


def test_repealed_instrument_cannot_be_current_after_repeal():
    source, doc, versions = repealed_instrument()
    # within the operative window (before repeal_date) the instrument is current
    assert is_currently_operational(doc, date(2023, 5, 1), versions) is True
    assert is_currently_operational(doc, date(2024, 6, 1), versions) is False
    assert effective_status(doc, date(2024, 6, 1), versions) == STATUS_REPEALED


def test_draft_is_never_current():
    source, doc, versions = draft_regulation()
    assert is_currently_operational(doc, date(2099, 1, 1)) is False
    assert effective_status(doc, date(2099, 1, 1)) == STATUS_DRAFT


def test_uncommenced_not_current():
    source, doc, versions = land_admin_2025()
    # before commencement (July 2025) the statute has no current version
    assert is_currently_operational(doc, date(2025, 6, 15), versions) is False
    assert effective_status(doc, date(2025, 6, 15), versions) == STATUS_NOT_IN_FORCE


def test_operational_status_persisted_for_unclear_label():
    source, doc, versions = land_admin_2025()
    # current_status is 'operative' (unclear) but a version resolves -> in_force
    assert doc.current_status == "operative"
    assert is_currently_operational(doc, date(2026, 6, 15), versions) is True
    assert persistable_operational_status(doc, date(2026, 6, 15), versions) == STATUS_IN_FORCE


def test_generic_doc_is_not_primary_law():
    source, doc, versions = generic_private_doc()
    assert source.source_class == "firm_private"
    assert is_primary_law(doc, source) is False
    assert primary_law_authority_tier(doc, source) == 0
    # even with an authority_tier set, a firm_private source cannot make it primary
    doc2 = doc.model_copy(update={"authority_tier": 5})
    assert primary_law_authority_tier(doc2, source) == 0


def test_public_primary_requires_explicit_status():
    source, doc, versions = land_admin_2025()
    assert source.source_class == "public_primary"
    assert is_primary_law(doc, source) is True
    assert primary_law_authority_tier(doc, source) == 5

    # A public_primary document with 'unknown' status is NOT primary law.
    unknown = doc.model_copy(update={"current_status": "unknown"})
    assert is_primary_law(unknown, source) is False
    assert primary_law_authority_tier(unknown, source) == 0


def test_primary_law_requires_registered_public_primary_source():
    source, doc, versions = generic_private_doc()
    # reclassify the source as public_primary manually to prove the rule
    public = source.model_copy(update={"source_class": "public_primary"})
    doc2 = doc.model_copy(update={"current_status": "commenced", "authority_tier": 4})
    assert is_primary_law(doc2, public) is True
    assert primary_law_authority_tier(doc2, public) == 4


def test_passage_citations_resolve_to_immutable_pinpoint():
    source, doc, versions = land_admin_2025()
    v2 = versions[1]
    passage = resolve_passage(v2, v2.passages, "section", "4")
    assert passage is not None
    pinpoint = stable_pinpoint(passage, v2)
    # stable = immutable version id + structural locator + passage hash
    assert pinpoint["version_id"] == v2.id
    assert pinpoint["version_label"] == "2.0"
    assert pinpoint["locator_kind"] == "section"
    assert pinpoint["locator_value"] == "4"
    assert pinpoint["passage_hash"] == passage.passage_hash
    # the same resolution is reproducible
    assert stable_pinpoint(passage, v2) == pinpoint


def test_lock_scope_requires_explicit_as_of_date():
    source, doc, versions = land_admin_2025()
    scope = RetrievalScope(jurisdiction="Kenya", source_classes=("public_primary",))
    with pytest.raises(TemporalError, match="explicit as_of_date"):
        lock_scope(scope, doc, source, versions)


def test_lock_scope_pins_one_version_before_expansion():
    source, doc, versions = land_admin_2025()
    scope = RetrievalScope(jurisdiction="Kenya", source_classes=("public_primary",))
    locked = lock_scope(scope, doc, source, versions, as_of_date=date(2026, 6, 15))
    assert locked.version_label == "2.0"
    assert locked.status == STATUS_IN_FORCE
    expansion = expansion_inherits(locked, expansion_index=1)
    assert expansion["version_id"] == locked.version_id
    assert expansion["locked"] is True
    assert expansion["expansion_index"] == 1


def test_lock_scope_rejects_wrong_jurisdiction():
    source, doc, versions = land_admin_2025()
    scope = RetrievalScope(jurisdiction="Tanzania")
    with pytest.raises(TemporalError, match="jurisdiction mismatch"):
        lock_scope(scope, doc, source, versions, as_of_date=date(2026, 6, 15))


def test_lock_scope_rejects_unsatisfied_authority_tiers():
    source, doc, versions = land_admin_2025()
    scope = RetrievalScope(authority_tiers=(2,))
    with pytest.raises(TemporalError, match="authority_tier mismatch"):
        lock_scope(scope, doc, source, versions, as_of_date=date(2026, 6, 15))


def test_lock_scope_validates_matter_scope_format_only():
    scope = RetrievalScope(matter_scope="M-2026-001")
    scope.validate()  # no exception locally; DB-side validation is caller's job
    with pytest.raises(TemporalError):
        RetrievalScope(authority_tiers=(9,)).validate()
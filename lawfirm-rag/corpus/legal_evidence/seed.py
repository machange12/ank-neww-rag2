"""Seed fixtures for the legal-evidence corpus — ALL FICTIONAL.

No external fetching, no real law. Used by tests and as a manual seeding
reference (see docs/architecture/legal-evidence-model.md). Fictional content
only; never ingest Kenya Law / NCLR / Gazette / Parliament (rights-gated —
schema/registry/manual-seed interfaces only).

Fixtures:
  * ``land_admin_2025`` — a fictional Kenyan statute with TWO immutable
    versions (v1.0 and v2.0) where one provision (section 4) is changed. The
    corpus is currently 'operative' with an unclear label and should resolve as
    'in_force'.
  * ``repealed_instrument`` — a fictional commencement-then-repeal instrument
    with no current version after its repeal date.
  * ``draft_regulation`` — a fictional DRAFT instrument (never current).
  * ``generic_private_doc`` — a generic / firm-private document that must NOT
    be treated as primary law.
"""
from __future__ import annotations

import hashlib
from datetime import date

from corpus.legal_evidence.models import (
    LegalDocument,
    LegalDocumentVersion,
    LegalPassage,
    LegalSource,
)


def _hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _passage(version_id: str, kind: str, value: str, text: str) -> LegalPassage:
    return LegalPassage(
        version_id=version_id,
        locator_kind=kind,
        locator_value=value,
        locator_path=value,
        rendered_text=text,
        normalized_text=" ".join(text.lower().split()),
        passage_hash=_hash(text),
        parser_confidence=1.0,
        vector_searchable=True,
    )


def land_admin_2025() -> tuple[LegalSource, LegalDocument, list[LegalDocumentVersion]]:
    """Fictional Kenyan statute, two versions, one changed provision (s.4)."""
    source = LegalSource(
        id="f0000000-0000-0000-0000-00000000000a",
        canonical_name="Fictional Statutory Corpus (Kenya)",
        publisher_issuer="Fictional Government Printer",
        jurisdiction="Kenya",
        source_class="public_primary",
        rights_status="permitted",
        ingestion_enabled=False,
    )
    document = LegalDocument(
        id="f0000000-0000-0000-0000-00000000000b",
        source_id=source.id,
        canonical_identifier="FA/LADRA/2025",
        title="Land Administration (Digital Records) Act, 2025",
        document_type="statute",
        jurisdiction="Kenya",
        authority_tier=5,
        language="en",
        binding_status="binding",
        published_date=date(2025, 6, 1),
        current_status="operative",  # unclear label — must still be persisted as in_force
    )

    s1 = "This Act applies to the digital registration of land records in the Republic."
    s4_v1 = (
        "4. The Registrar shall maintain a national digital register of all land "
        "records and shall issue digital certificates upon registration."
    )
    s4_v2 = (
        "4. The Registrar shall maintain a national digital register of all land "
        "records, issue digital certificates upon registration, and publish an "
        "annual audit of the register to the public."
    )
    s9 = "Any person aggrieved by a decision of the Registrar may appeal to the High Court."

    v1 = LegalDocumentVersion(
        id="f0000000-0000-0000-0000-00000000001a",
        document_id=document.id,
        version_label="1.0",
        source_hash=_hash(s4_v1 + s1 + s9),
        publication_date=date(2025, 6, 1),
        commencement_date=date(2025, 7, 1),
        valid_from=date(2025, 7, 1),
        valid_to=date(2026, 5, 31),
        parser_version="seed-1",
        parser_confidence=1.0,
        ingest_status="parsed",
        passages=[
            _passage("f0000000-0000-0000-0000-00000000001a", "section", "1", s1),
            _passage("f0000000-0000-0000-0000-00000000001a", "section", "4", s4_v1),
            _passage("f0000000-0000-0000-0000-00000000001a", "section", "9", s9),
        ],
    )
    v2 = LegalDocumentVersion(
        id="f0000000-0000-0000-0000-00000000001b",
        document_id=document.id,
        version_label="2.0",
        source_hash=_hash(s4_v2 + s1 + s9),
        publication_date=date(2026, 5, 1),
        commencement_date=date(2026, 6, 1),
        valid_from=date(2026, 6, 1),
        valid_to=None,
        supersedes_version_id=v1.id,
        parser_version="seed-1",
        parser_confidence=1.0,
        ingest_status="parsed",
        passages=[
            _passage("f0000000-0000-0000-0000-00000000001b", "section", "1", s1),
            _passage("f0000000-0000-0000-0000-00000000001b", "section", "4", s4_v2),
            _passage("f0000000-0000-0000-0000-00000000001b", "section", "9", s9),
        ],
    )
    return source, document, [v1, v2]


def repealed_instrument() -> tuple[LegalSource, LegalDocument, list[LegalDocumentVersion]]:
    """Fictional commencement-then-repeal instrument (no current version after repeal)."""
    source = LegalSource(
        id="f0000000-0000-0000-0000-00000000000c",
        canonical_name="Fictional Statutory Corpus (Kenya)",
        publisher_issuer="Fictional Government Printer",
        jurisdiction="Kenya",
        source_class="public_primary",
        rights_status="permitted",
        ingestion_enabled=False,
    )
    document = LegalDocument(
        id="f0000000-0000-0000-0000-00000000000d",
        source_id=source.id,
        canonical_identifier="FA/PEN/2023",
        title="Fictional Pandemic Levy Order, 2023",
        document_type="statutory_instrument",
        jurisdiction="Kenya",
        authority_tier=3,
        current_status="repealed",
        published_date=date(2023, 1, 1),
    )
    body = "A levy of two percent is imposed on digital financial transfers for one year."
    version = LegalDocumentVersion(
        id="f0000000-0000-0000-0000-00000000001c",
        document_id=document.id,
        version_label="1.0",
        source_hash=_hash(body),
        publication_date=date(2023, 1, 1),
        commencement_date=date(2023, 2, 1),
        valid_from=date(2023, 2, 1),
        repeal_date=date(2024, 2, 1),
        parser_version="seed-1",
        parser_confidence=1.0,
        ingest_status="parsed",
        passages=[_passage("f0000000-0000-0000-0000-00000000001c", "section", "1", body)],
    )
    return source, document, [version]


def draft_regulation() -> tuple[LegalSource, LegalDocument, list[LegalDocumentVersion]]:
    """Fictional DRAFT regulation — never current."""
    source = LegalSource(
        id="f0000000-0000-0000-0000-00000000000e",
        canonical_name="Fictional Statutory Corpus (Kenya)",
        publisher_issuer="Fictional Government Printer",
        jurisdiction="Kenya",
        source_class="public_primary",
        rights_status="permitted",
        ingestion_enabled=False,
    )
    document = LegalDocument(
        id="f0000000-0000-0000-0000-00000000000f",
        source_id=source.id,
        canonical_identifier="FA/DR/2026",
        title="Fictional Digital Identity Regulations, 2026 (Draft)",
        document_type="regulation",
        jurisdiction="Kenya",
        authority_tier=4,
        current_status="draft",
    )
    body = "Every digital identity holder shall verify their biometrics every five years."
    version = LegalDocumentVersion(
        id="f0000000-0000-0000-0000-00000000001d",
        document_id=document.id,
        version_label="0.1",
        source_hash=_hash(body),
        parser_version="seed-1",
        parser_confidence=1.0,
        ingest_status="parsed",
        passages=[_passage("f0000000-0000-0000-0000-00000000001d", "section", "1", body)],
    )
    return source, document, [version]


def generic_private_doc() -> tuple[LegalSource, LegalDocument, list[LegalDocumentVersion]]:
    """Fictional generic / firm-private document — never primary law."""
    source = LegalSource(
        id="f0000000-0000-0000-0000-000000000010",
        canonical_name="Firm private repository",
        jurisdiction="Kenya",
        source_class="firm_private",
        rights_status="permitted",
        ingestion_enabled=False,
    )
    document = LegalDocument(
        id="f0000000-0000-0000-0000-000000000011",
        source_id=source.id,
        canonical_identifier="AK/INT/0042",
        title="Internal Research Memo on Registration Delays",
        document_type="memo",
        jurisdiction="Kenya",
        authority_tier=0,
        current_status="unknown",
    )
    body = "This memo is an internal working note, not a legal instrument."
    version = LegalDocumentVersion(
        id="f0000000-0000-0000-0000-00000000001e",
        document_id=document.id,
        version_label="1.0",
        source_hash=_hash(body),
        parser_version="seed-1",
        parser_confidence=1.0,
        ingest_status="parsed",
        passages=[_passage("f0000000-0000-0000-0000-00000000001e", "paragraph", "1", body)],
    )
    return source, document, [version]


ALL_FIXTURES = (
    land_admin_2025,
    repealed_instrument,
    draft_regulation,
    generic_private_doc,
)
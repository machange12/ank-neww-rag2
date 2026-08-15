"""Status tooling for the legal corpus.

The database stores a free ``current_status`` label (unknown / operative /
draft / commenced / repealed / amended / not_in_force / superseded). These
helpers compute the OPERATIVE status for an ``as_of_date`` deterministically
and conservatively — an unclear label is never treated as operational.

Status vocabulary used by the tooling (exact strings):
    unknown / draft / commenced / repealed / in_force / not_in_force
"""
from __future__ import annotations

from datetime import date
from typing import Iterable

from corpus.legal_evidence.models import LegalDocument, LegalDocumentVersion
from corpus.legal_evidence.versions import resolve_version

STATUS_UNKNOWN = "unknown"
STATUS_DRAFT = "draft"
STATUS_COMMENCED = "commenced"
STATUS_REPEALED = "repealed"
STATUS_IN_FORCE = "in_force"
STATUS_NOT_IN_FORCE = "not_in_force"

# Labels that mean the instrument is never current at the document level.
# Draft is the only such label: a repealed/superseded instrument WAS current
# for as-of dates before its repeal, so those are gated by version dates
# (``repeal_date``/``supersedes_version_id``) instead of a blanket label check.
NEVER_CURRENT_LABELS = {STATUS_DRAFT}
REPEALED_LABELS = {STATUS_REPEALED, "superseded"}


def is_currently_operational(
    document: LegalDocument,
    as_of_date: date,
    versions: Iterable[LegalDocumentVersion] | None = None,
) -> bool:
    """True when ``document`` has an effective version on ``as_of_date``.

    A draft document is never current. A repealed/superseded instrument is not
    current on dates after its repeal (no effective version resolves) but IS
    current for dates within its operative window.
    """
    if document.current_status in NEVER_CURRENT_LABELS:
        return False
    version, _ = resolve_version(document, as_of_date, versions)
    return version is not None


def effective_status(
    document: LegalDocument,
    as_of_date: date,
    versions: Iterable[LegalDocumentVersion] | None = None,
) -> str:
    """Operative status of ``document`` on ``as_of_date`` (deterministic).

    Rules:
      * draft                       -> ``draft`` (never current)
      * a version resolves on the date AND the label is ``commenced``
                                    -> ``commenced``
      * a version resolves on the date
                                    -> ``in_force``
      * no version resolves AND label is repealed/superseded
                                    -> ``repealed``
      * no version resolves        -> ``not_in_force`` (covers uncommenced)
    """
    if document.current_status == STATUS_DRAFT:
        return STATUS_DRAFT
    version, _ = resolve_version(document, as_of_date, versions)
    if version is not None:
        if document.current_status == STATUS_COMMENCED:
            return STATUS_COMMENCED
        return STATUS_IN_FORCE
    if document.current_status in REPEALED_LABELS:
        return STATUS_REPEALED
    return STATUS_NOT_IN_FORCE


def persistable_operational_status(
    document: LegalDocument,
    as_of_date: date,
    versions: Iterable[LegalDocumentVersion] | None = None,
) -> str:
    """Status to PERSIST for a currently-operational law with an unclear label.

    When a document is operationally live on ``as_of_date`` but its stored
    label is unclear (``unknown`` / ``operative``), we persist an explicit
    status so downstream consumers are not forced to interpret an ambiguous
    label: ``in_force`` (or ``commenced`` when the label says commenced).
    Non-operational documents return ``effective_status``.
    """
    if not is_currently_operational(document, as_of_date, versions):
        return effective_status(document, as_of_date, versions)
    if document.current_status in (STATUS_UNKNOWN, "operative"):
        return STATUS_IN_FORCE
    return effective_status(document, as_of_date, versions)
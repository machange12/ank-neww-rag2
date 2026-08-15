"""Deterministic version resolution (no cross-module imports beyond models)."""
from __future__ import annotations

from datetime import date
from typing import Iterable

from corpus.legal_evidence.models import LegalDocument, LegalDocumentVersion

STATUS_DRAFT = "draft"


def _version_effective_on(version: LegalDocumentVersion, d: date) -> bool:
    """Is this immutable version the operative text on ``d``?

    Conservative: an unknown/absent date is treated as permissive (NULL = open
    range), but a date that is KNOWN and inconsistent excludes the version.
    """
    if version.valid_from and version.valid_from > d:
        return False
    if version.valid_to and version.valid_to < d:
        return False
    if version.repeal_date and version.repeal_date < d:
        return False
    if version.commencement_date and version.commencement_date > d:
        return False
    return True


def _version_recency_key(version: LegalDocumentVersion):
    """Deterministic ordering key for tie-breaks among effective versions."""
    candidate = (
        version.commencement_date
        or version.effective_date
        or version.valid_from
        or version.publication_date
        or date.min
    )
    return (candidate, version.version_label)


def resolve_version(
    document: LegalDocument,
    as_of_date: date,
    versions: Iterable[LegalDocumentVersion] | None = None,
) -> tuple[LegalDocumentVersion | None, str]:
    """Pick the single version of ``document`` that is effective on ``as_of_date``.

    Returns ``(version, reason)``. Deterministic rules:
      1. A draft document never has a current version (its text is unstable).
      2. Among versions effective on the date, prefer the most recently
         commenced / effective / valid-from one (the latest operative text).
      3. Ties are broken by ``version_label``.
    """
    if document.current_status == STATUS_DRAFT:
        return None, "document is a draft; draft text is never current"

    versions = list(versions or [])
    effective = [v for v in versions if _version_effective_on(v, as_of_date)]
    if not effective:
        return None, "no version is effective on the as-of date"
    effective.sort(key=_version_recency_key, reverse=True)
    return effective[0], "latest effective version"
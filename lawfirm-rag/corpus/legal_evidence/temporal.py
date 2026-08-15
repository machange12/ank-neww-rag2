"""Temporal retrieval contract for the legal corpus.

Two concepts:

1. ``RetrievalScope`` — the caller's requested research scope. ``as_of_date``
   is optional here but MUST be made explicit (resolved to a concrete date)
   before any retrieval; there is no silent "today" default inside the lock.
   ``matter_scope`` is recorded but MUST be validated server/db-side by the
   caller (pure module cannot hold RLS).

2. ``lock_scope`` — deterministically resolves an ``as_of_date`` to exactly ONE
   immutable document version (using ``valid_from``/``valid_to`` and the
   publication/commencement/effective/repeal dates) BEFORE any LLM expansion.
   Expansions inherit the locked version; they never re-resolve. The locked
   scope is recorded in ``retrieval_events`` for auditability.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable

from corpus.legal_evidence.models import (
    PRIMARY_LAW_CLASS,
    LegalDocument,
    LegalDocumentVersion,
    LegalPassage,
    LegalSource,
)
from corpus.legal_evidence.status import (
    STATUS_DRAFT,
    effective_status,
    is_currently_operational,
)
from corpus.legal_evidence.versions import (
    _version_effective_on,
    _version_recency_key,
    resolve_version,
)


class TemporalError(ValueError):
    """Raised when an as-of date cannot be resolved to a document version."""


@dataclass(frozen=True)
class RetrievalScope:
    """Validated, caller-supplied research scope. ``as_of_date`` is optional at
    construction but MUST be provided (explicitly) at ``lock_scope`` time."""

    jurisdiction: str | None = None
    as_of_date: date | None = None
    source_classes: tuple[str, ...] = ()
    authority_tiers: tuple[int, ...] = ()
    court_or_issuer: str | None = None
    document_types: tuple[str, ...] = ()
    matter_scope: str | None = None

    def validate(self) -> None:
        """Local format validation.

        ``matter_scope`` cannot be validated here (no RLS); the caller MUST
        validate it server/db-side before use. ``as_of_date`` is validated at
        lock time (must be explicit).
        """
        if not all(1 <= tier <= 5 for tier in self.authority_tiers):
            raise TemporalError("authority_tiers must be within 1..5")


@dataclass(frozen=True)
class LockedScope:
    """The immutable, auditable result of locking a retrieval scope."""

    as_of_date: date
    jurisdiction: str | None = None
    source_class: str = ""
    authority_tier: int = 0
    document_id: str = ""
    document_title: str = ""
    version_id: str = ""
    version_label: str = ""
    status: str = ""
    scope: RetrievalScope = field(default_factory=RetrievalScope)
    locked_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        """Serialisable locked scope recorded in retrieval_events."""
        return {
            "as_of_date": self.as_of_date.isoformat(),
            "jurisdiction": self.jurisdiction,
            "source_class": self.source_class,
            "authority_tier": self.authority_tier,
            "document_id": self.document_id,
            "document_title": self.document_title,
            "version_id": self.version_id,
            "version_label": self.version_label,
            "status": self.status,
            "locked": True,
            "matter_scope": self.scope.matter_scope,
        }


def lock_scope(
    scope: RetrievalScope,
    document: LegalDocument,
    source: LegalSource | None,
    versions: Iterable[LegalDocumentVersion],
    as_of_date: date | None = None,
) -> LockedScope:
    """Deterministically lock a scope to ONE immutable document version.

    MUST run BEFORE any LLM expansion; expansions inherit the locked version_id
    and never re-resolve. Raises ``TemporalError`` when the scope cannot be
    satisfied (no effective version, or the registered source/document cannot
    satisfy the requested filters).
    """
    if as_of_date is None:
        raise TemporalError(
            "lock_scope requires an explicit as_of_date (pass date.today() for 'now'); "
            "there is no silent default."
        )
    scope.validate()

    if scope.jurisdiction and source and source.jurisdiction not in (scope.jurisdiction, None):
        raise TemporalError(
            f"jurisdiction mismatch: source is {source.jurisdiction!r}, scope requires {scope.jurisdiction!r}"
        )
    if scope.source_classes and source is not None and source.source_class not in scope.source_classes:
        raise TemporalError(
            f"source_class mismatch: source is {source.source_class!r}, scope requires {scope.source_classes}"
        )
    if scope.authority_tiers and document.authority_tier not in scope.authority_tiers:
        raise TemporalError(
            f"authority_tier mismatch: document tier is {document.authority_tier}, scope requires {scope.authority_tiers}"
        )
    if scope.document_types and document.document_type not in scope.document_types:
        raise TemporalError(
            f"document_type mismatch: document is {document.document_type!r}, scope requires {scope.document_types}"
        )

    version, reason = resolve_version(document, as_of_date, versions)
    if version is None:
        raise TemporalError(
            f"no effective version of {document.title!r} as of {as_of_date.isoformat()}: {reason}"
        )

    return LockedScope(
        as_of_date=as_of_date,
        jurisdiction=scope.jurisdiction,
        source_class=source.source_class if source else "",
        authority_tier=document.authority_tier,
        document_id=document.id or "",
        document_title=document.title,
        version_id=version.id or "",
        version_label=version.version_label,
        status=effective_status(document, as_of_date, versions),
        scope=scope,
    )


def expansion_inherits(locked: LockedScope, expansion_index: int = 0) -> dict[str, Any]:
    """The scope an LLM query expansion must inherit.

    Returns the same pinned version/locked scope so every expansion recorded in
    ``retrieval_events.query_expansion`` resolves against the SAME immutable
    version, never a re-resolution.
    """
    return {**locked.to_dict(), "expansion_index": expansion_index}


def resolve_passage(
    version: LegalDocumentVersion,
    passages: Iterable[LegalPassage],
    locator_kind: str,
    locator_value: str,
) -> LegalPassage | None:
    """Resolve a structural locator to a passage within an immutable version."""
    for passage in passages:
        if (
            passage.version_id == (version.id or version.version_label)
            and passage.locator_kind == locator_kind
            and passage.locator_value == locator_value
        ):
            return passage
    for passage in passages:
        if passage.locator_kind == locator_kind and passage.locator_value == locator_value:
            return passage
    return None


def stable_pinpoint(passage: LegalPassage, version: LegalDocumentVersion) -> dict[str, str]:
    """A citation pinpoint that is STABLE across runs.

    It points at the immutable version id + structural locator + passage hash —
    never at mutable text. Reproducible and safe for a citation record.
    """
    return {
        "version_id": passage.version_id,
        "version_label": version.version_label,
        "locator_kind": passage.locator_kind,
        "locator_value": passage.locator_value,
        "passage_hash": passage.passage_hash,
    }


def is_primary_law(document: LegalDocument, source: LegalSource | None = None) -> bool:
    """A document is primary law ONLY when its source is explicitly registered
    as ``public_primary`` (not denied) AND it carries an explicit status.

    A generic / firm_private document (or one with an ``unknown`` status) is
    never primary law and never receives an authority tier.
    """
    if source is None or not source.is_primary_law_registered():
        return False
    if document.current_status in ("", "unknown"):
        return False
    return True


def primary_law_authority_tier(document: LegalDocument, source: LegalSource | None = None) -> int:
    """Authority tier usable for primary-law ranking, else 0.

    Generic documents cannot be primary law / receive an authority tier without
    an explicitly registered ``source_class='public_primary'`` AND an explicit
    status label. When either condition fails the tier is 0.
    """
    if not is_primary_law(document, source):
        return 0
    return document.authority_tier
"""Typed models for the legal-evidence corpus (mirror of migration 0002).

These mirror the 0002 tables. They are intended for internal tooling, seeding
and deterministic logic — the database is the system of record.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

PRIMARY_LAW_CLASS = "public_primary"

SOURCE_CLASSES = (
    "public_primary",
    "licensed_secondary",
    "firm_private",
    "regulatory",
    "parliamentary",
)

RIGHTS_STATUSES = ("unreviewed", "reviewing", "permitted", "denied")

DOCUMENT_STATUSES = (
    "unknown",
    "operative",
    "draft",
    "commenced",
    "repealed",
    "amended",
    "not_in_force",
    "superseded",
)

RELATIONSHIPS = (
    "amends",
    "repeals",
    "commences",
    "implements",
    "follows",
    "distinguishes",
    "overrules",
    "considers",
    "cites",
)


class LegalSource(BaseModel):
    """One row of public.legal_sources — publisher / corpus registry."""

    id: str | None = None
    canonical_name: str
    publisher_issuer: str | None = None
    jurisdiction: str | None = None
    source_class: str = "firm_private"
    rights_status: str = "unreviewed"
    licence_notes: str | None = None
    canonical_base_url: str | None = None
    ingestion_enabled: bool = False
    owner_user_id: str | None = None
    tenant_id: str | None = None
    created_at: datetime | None = None

    def is_primary_law_registered(self) -> bool:
        return self.source_class == PRIMARY_LAW_CLASS and self.rights_status != "denied"


class LegalDocument(BaseModel):
    """One row of public.legal_documents — one per instrument/judgment, not per version."""

    id: str | None = None
    source_id: str | None = None
    canonical_identifier: str | None = None
    akn_uri: str | None = None
    title: str
    document_type: str = "unknown"
    jurisdiction: str | None = None
    authority_tier: int = 0
    issuer_court: str | None = None
    neutral_citation: str | None = None
    legacy_citation: str | None = None
    language: str = "en"
    binding_status: str = "unknown"
    published_date: date | None = None
    retrieved_date: datetime | None = None
    current_status: str = "unknown"
    tenant_id: str | None = None
    created_at: datetime | None = None


class LegalDocumentVersion(BaseModel):
    """One row of public.legal_document_versions — IMMUTABLE.

    Text/url/hash of a row is never updated; a change is a new row.
    """

    id: str | None = None
    document_id: str
    version_label: str = "1.0"
    source_url: str | None = None
    source_hash: str
    original_file_ref: str | None = None
    parser_version: str = "unknown"
    parser_confidence: float = 0
    valid_from: date | None = None
    valid_to: date | None = None
    publication_date: date | None = None
    assent_date: date | None = None
    commencement_date: date | None = None
    effective_date: date | None = None
    repeal_date: date | None = None
    supersedes_version_id: str | None = None
    ingest_status: str = "pending"
    passages: list["LegalPassage"] = Field(default_factory=list)


class LegalPassage(BaseModel):
    """One row of public.legal_passages — a stable structural unit within an immutable version."""

    id: str | None = None
    version_id: str
    locator_kind: str
    locator_value: str
    locator_path: str
    rendered_text: str
    normalized_text: str
    page_number: int | None = None
    passage_hash: str
    embedding_ref: str | None = None
    vector_searchable: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    parser_confidence: float = 0


class CitationRecord(BaseModel):
    """One row of public.citation_records — verification result + reviewer override."""

    id: str | None = None
    research_run_id: str | None = None
    passage_id: str | None = None
    document_version_id: str | None = None
    proposition_text: str
    proposition_span: str | None = None
    displayed_citation: str | None = None
    pinpoint: str | None = None
    quote_span: str | None = None
    citation_status: str = "unavailable"
    verification_method: str | None = None
    verifier_version: str = "unknown"
    verifier_evidence: dict[str, Any] = Field(default_factory=dict)
    thresholds: dict[str, Any] = Field(default_factory=dict)
    reviewer_override: dict[str, Any] | None = None
    created_at: datetime | None = None


class SourceRelationship(BaseModel):
    """One row of public.source_relationships — authority graph links between versions."""

    id: str | None = None
    from_version_id: str
    to_version_id: str | None = None
    relationship: str = "cites"
    provenance: str | None = None
    confidence: float = 0
    created_at: datetime | None = None
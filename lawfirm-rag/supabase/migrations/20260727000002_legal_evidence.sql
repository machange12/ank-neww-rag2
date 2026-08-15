-- ============================================================
-- Migration 0002 — legal evidence & temporal-source schema
-- ------------------------------------------------------------
-- First-class legal provenance, kept SEPARATE from the firm
-- document corpus (public.documents / document_metadata).
--
-- Principles enforced by this schema:
--   * Public/legal corpus is distinct from firm-private corpus;
--     each has independent rights, retention and citation policy.
--   * Every legal source version is IMMUTABLE. Text is never
--     overwritten in place; a new version is a new row.
--   * Unknown dates/statuses are explicit (NULL or an explicit
--     status); nothing is fabricated.
--   * A parsed passage is NOT a substitute for the original source
--     file. Original-file reference + source hash are stored.
--   * A source can be registered with ingestion_enabled=false for
--     rights review without any crawling.
--   * Passage citations resolve to an immutable version + stable
--     structural locator + passage hash.
-- ============================================================

-- ------------------------------------------------------------
-- legal_sources — publisher / corpus registry
-- ------------------------------------------------------------
create table if not exists public.legal_sources
(
  id                    uuid primary key default gen_random_uuid(),
  canonical_name        text not null,
  publisher_issuer      text,
  jurisdiction          text,
  source_class          text not null default 'firm_private',
  rights_status         text not null default 'unreviewed',
  licence_notes         text,
  canonical_base_url    text,
  ingestion_enabled     boolean not null default false,
  owner_user_id         uuid,
  tenant_id             uuid references public.tenants(id),
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  constraint legal_sources_class_check check (
    source_class in (
      'public_primary',      -- statutes, judgments, gazette (rights-gated)
      'licensed_secondary',  -- licensed commentary / reporters
      'firm_private',        -- firm-authored legal work-product
      'regulatory',          -- guidance, circulars
      'parliamentary'        -- Hansard / bills / committee reports
    )
  ),
  constraint legal_sources_rights_check check (
    rights_status in ('unreviewed', 'reviewing', 'permitted', 'denied')
  )
);
create index if not exists legal_sources_class_idx on public.legal_sources (source_class);
create index if not exists legal_sources_ingest_idx on public.legal_sources (ingestion_enabled);

-- ------------------------------------------------------------
-- legal_documents — canonical documents (one per instrument /
-- judgment / item), NOT per version.
-- ------------------------------------------------------------
create table if not exists public.legal_documents
(
  id                     uuid primary key default gen_random_uuid(),
  source_id              uuid references public.legal_sources(id) on delete cascade,
  canonical_identifier   text,
  akn_uri                text,
  title                  text not null,
  document_type          text not null default 'unknown',
  jurisdiction           text,
  authority_tier         integer not null default 0,
  issuer_court           text,
  neutral_citation       text,
  legacy_citation        text,
  language               text not null default 'en',
  binding_status         text not null default 'unknown',
  published_date         date,
  retrieved_date         timestamptz,
  current_status         text not null default 'unknown',
  tenant_id              uuid references public.tenants(id),
  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now(),
  constraint legal_documents_status_check check (
    current_status in ('unknown', 'operative', 'draft', 'commenced', 'repealed', 'amended', 'not_in_force', 'superseded')
  ),
  constraint legal_documents_binding_check check (
    binding_status in ('binding', 'persuasive', 'non_binding', 'advisory', 'unknown')
  ),
  constraint legal_documents_tier_check check (
    authority_tier between 0 and 5
  )
);
create index if not exists legal_documents_source_idx on public.legal_documents (source_id);
create index if not exists legal_documents_canonical_idx on public.legal_documents (canonical_identifier);
create index if not exists legal_documents_type_idx on public.legal_documents (document_type);

-- ------------------------------------------------------------
-- legal_document_versions — IMMUTABLE versions of a document.
-- Never UPDATE the text/url/hash of a row here.
-- ------------------------------------------------------------
create table if not exists public.legal_document_versions
(
  id                      uuid primary key default gen_random_uuid(),
  document_id             uuid not null references public.legal_documents(id) on delete cascade,
  version_label           text not null default '1.0',
  source_url              text,
  source_hash             text not null,
  original_file_ref       text,
  parser_version          text not null default 'unknown',
  parser_confidence       numeric not null default 0,
  valid_from              date,
  valid_to                date,
  publication_date        date,
  assent_date             date,
  commencement_date       date,
  effective_date          date,
  repeal_date             date,
  supersedes_version_id   uuid references public.legal_document_versions(id),
  ingest_status           text not null default 'pending',
  created_at              timestamptz not null default now(),
  constraint legal_doc_versions_status_check check (
    ingest_status in ('pending', 'parsing', 'parsed', 'failed')
  )
);
create index if not exists legal_doc_versions_document_idx on public.legal_document_versions (document_id);
create index if not exists legal_doc_versions_valid_idx on public.legal_document_versions (valid_from, valid_to);

-- ------------------------------------------------------------
-- legal_passages — stable structural units (article/section/part/
-- paragraph/page) within an immutable version.
-- ------------------------------------------------------------
create table if not exists public.legal_passages
(
  id                 uuid primary key default gen_random_uuid(),
  version_id         uuid not null references public.legal_document_versions(id) on delete cascade,
  locator_kind       text not null,
  locator_value      text not null,
  locator_path       text not null,
  rendered_text      text not null,
  normalized_text    text not null,
  page_number        integer,
  passage_hash       text not null,
  embedding_ref      text,
  vector_searchable  boolean not null default false,
  metadata           jsonb not null default '{}'::jsonb,
  parser_confidence  numeric not null default 0,
  created_at         timestamptz not null default now(),
  unique (version_id, locator_kind, locator_value)
);
create index if not exists legal_passages_version_idx on public.legal_passages (version_id);
create index if not exists legal_passages_hash_idx on public.legal_passages (passage_hash);

-- ------------------------------------------------------------
-- citation_records — verification result + reviewer override.
-- The reviewer override NEVER mutates the original verification
-- evidence; it is a separate audit row.
-- ------------------------------------------------------------
create table if not exists public.citation_records
(
  id                    uuid primary key default gen_random_uuid(),
  research_run_id       uuid,
  passage_id            uuid references public.legal_passages(id),
  document_version_id   uuid references public.legal_document_versions(id),
  proposition_text      text not null,
  proposition_span      text,
  displayed_citation    text,
  pinpoint              text,
  quote_span            text,
  citation_status       text not null default 'unavailable',
  verification_method   text,
  verifier_version      text not null default 'unknown',
  verifier_evidence     jsonb not null default '{}'::jsonb,
  thresholds            jsonb not null default '{}'::jsonb,
  reviewer_override     jsonb,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  constraint citation_status_check check (
    citation_status in ('verified', 'weak', 'conflicting', 'unavailable')
  )
);
create index if not exists citation_records_run_idx on public.citation_records (research_run_id);
create index if not exists citation_records_passage_idx on public.citation_records (passage_id);

-- ------------------------------------------------------------
-- source_relationships — authority graph links
-- ------------------------------------------------------------
create table if not exists public.source_relationships
(
  id               uuid primary key default gen_random_uuid(),
  from_version_id  uuid not null references public.legal_document_versions(id) on delete cascade,
  to_version_id    uuid references public.legal_document_versions(id) on delete cascade,
  relationship     text not null,
  provenance       text,
  confidence       numeric not null default 0,
  created_at       timestamptz not null default now(),
  constraint source_rel_check check (
    relationship in (
      'amends', 'repeals', 'commences', 'implements', 'follows',
      'distinguishes', 'overrules', 'considers', 'cites'
    )
  )
);
create index if not exists source_rel_from_idx on public.source_relationships (from_version_id);
create index if not exists source_rel_to_idx on public.source_relationships (to_version_id);

-- ------------------------------------------------------------
-- legal_research_runs — reproducible audit trace per research run
-- ------------------------------------------------------------
create table if not exists public.legal_research_runs
(
  id                    uuid primary key default gen_random_uuid(),
  user_id               uuid not null,
  tenant_id             uuid references public.tenants(id),
  jurisdiction          text,
  as_of_date            date,
  source_classes        jsonb not null default '[]'::jsonb,
  authority_tiers       jsonb not null default '[]'::jsonb,
  court_or_issuer       text,
  document_types        jsonb not null default '[]'::jsonb,
  matter_scope          jsonb,
  locked_scope          jsonb not null default '{}'::jsonb,
  created_at            timestamptz not null default now()
);
create index if not exists legal_research_runs_user_idx on public.legal_research_runs (user_id);

-- ------------------------------------------------------------
-- retrieval_events — one row per retrieval call for auditability
-- ------------------------------------------------------------
create table if not exists public.retrieval_events
(
  id                    uuid primary key default gen_random_uuid(),
  research_run_id       uuid references public.legal_research_runs(id),
  user_id               uuid not null,
  query_text            text,
  query_expansion       jsonb not null default '[]'::jsonb,
  locked_scope          jsonb not null default '{}'::jsonb,
  returned_passage_ids  jsonb not null default '[]'::jsonb,
  created_at            timestamptz not null default now()
);
create index if not exists retrieval_events_run_idx on public.retrieval_events (research_run_id);

-- ------------------------------------------------------------
-- Rights: a source can be registered but NOT ingest-enabled.
-- Default for all Kenya external corpora is ingestion_enabled=false
-- until rights are approved (see docs/security/threat-model.md and
-- README data-source policy).
-- ============================================================
insert into public.legal_sources
  (canonical_name, publisher_issuer, jurisdiction, source_class, rights_status, ingestion_enabled)
values
  ('Kenya Law Reports (KenyaLaw.org)', 'Kenya Law', 'Kenya', 'public_primary', 'unreviewed', false),
  ('National Council for Law Reporting (NCLR)', 'NCLR', 'Kenya', 'public_primary', 'unreviewed', false),
  ('Kenya Gazette', 'Government of Kenya', 'Kenya', 'public_primary', 'unreviewed', false),
  ('Parliament of Kenya (National Assembly)', 'Parliament of Kenya', 'Kenya', 'parliamentary', 'unreviewed', false)
on conflict (id) do nothing;

-- ------------------------------------------------------------
-- Record migration version
-- ------------------------------------------------------------
insert into public.schema_migrations (version, description)
values ('20260727000002', 'legal evidence & temporal-source schema (legal_sources/documents/versions/passages, citation_records, source_relationships, research runs, retrieval events)')
on conflict (version) do nothing;
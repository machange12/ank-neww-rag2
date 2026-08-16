-- ================================================================
-- Migration 0007: fix documents.embedding dimension (1536 -> 768)
--
-- BUG (confirmed, not hypothetical): the baseline schema declared
-- `embedding vector(1536)`, matching OpenAI's text-embedding-3-small.
-- But the app's default embedder path (providers.get_embeddings(),
-- used whenever OPENAI_API_KEY is unset) is the local HuggingFace
-- nomic-embed-text-v1.5 model, whose native/max output is 768-dim —
-- confirmed empirically by loading the model and embedding a query.
-- pgvector enforces exact column dimension on insert, so every
-- ingest attempt via the local embedder was failing at the
-- `documents` insert. See providers.py for the accompanying fix that
-- pins both embedder paths (local HF via truncate_dim, OpenAI via
-- dimensions=) to settings.embedding_dim (768), so switching
-- providers never requires another schema change.
--
-- DESTRUCTIVE: pgvector cannot reinterpret 1536-dim data as 768-dim,
-- so this truncates `documents` first. Given the bug above, any rows
-- that exist were almost certainly inserted under a different,
-- inconsistent configuration and are not safely reusable anyway.
-- Re-ingest (Drive re-sync or re-upload) after applying this.
--
-- Idempotent: safe to re-run (drops/recreates use IF EXISTS /
-- CREATE OR REPLACE).
-- ================================================================

truncate table public.documents;

drop index if exists public.documents_embedding_idx;

alter table public.documents
  alter column embedding type vector(768);

create index if not exists documents_embedding_idx
  on public.documents using ivfflat (embedding vector_cosine_ops)
  with (lists = 100);

-- ------------------------------------------------------------
-- Record migration version
-- ------------------------------------------------------------
insert into public.schema_migrations (version, description)
values ('20260727000007', 'fix documents.embedding dimension 1536 -> 768 (matches local embedder)')
on conflict (version) do nothing;

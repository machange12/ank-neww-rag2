-- ================================================================
-- Migration 0007: fix documents.embedding dimension (-> 768)
--
-- BUG (confirmed, not hypothetical): the baseline schema declared
-- `embedding vector(1536)`, matching OpenAI's text-embedding-3-small.
-- But the app's default embedder path (providers.get_embeddings(),
-- used whenever OPENAI_API_KEY is unset) is the local HuggingFace
-- nomic-embed-text-v1.5 model, whose native/max output is 768-dim —
-- confirmed empirically by loading the model and embedding a query.
-- pgvector enforces exact column dimension on insert, so every
-- ingest attempt via the local embedder was failing at the
-- `documents` insert on a fresh vector(1536) baseline. See providers.py
-- for the accompanying fix that pins both embedder paths (local HF via
-- truncate_dim, OpenAI via dimensions=) to settings.embedding_dim (768),
-- so switching providers never requires another schema change.
--
-- CONDITIONAL / non-destructive when already correct: on at least one
-- live deployment the `documents.embedding` column was already
-- vector(768) (evolved outside this migration set, before
-- schema_migrations existed to track it) — an unconditional truncate
-- would have destroyed real, already-correct data for no reason. This
-- only truncates+retypes when the live dimension actually differs from
-- 768. pgvector cannot reinterpret data at a different dimension, so
-- the truncate is unavoidable in the case where it DOES need to run.
--
-- Idempotent: safe to re-run.
-- ================================================================

do $$
declare
  current_dim int;
begin
  select atttypmod into current_dim
  from pg_attribute a
  join pg_class c on a.attrelid = c.oid
  join pg_namespace n on c.relnamespace = n.oid
  where n.nspname = 'public' and c.relname = 'documents' and a.attname = 'embedding';

  if current_dim is distinct from 768 then
    raise notice 'documents.embedding dimension is % (expected 768) — truncating and re-typing. Re-ingest required after this migration.', current_dim;
    execute 'truncate table public.documents';
    execute 'drop index if exists public.documents_embedding_idx';
    execute 'alter table public.documents alter column embedding type vector(768)';
    execute 'create index if not exists documents_embedding_idx on public.documents using ivfflat (embedding vector_cosine_ops) with (lists = 100)';
  else
    raise notice 'documents.embedding is already vector(768) — no change needed, no data touched.';
  end if;
end $$;

-- ------------------------------------------------------------
-- Record migration version
-- ------------------------------------------------------------
insert into public.schema_migrations (version, description)
values ('20260727000007', 'fix documents.embedding dimension -> 768 (matches local embedder), conditional/non-destructive when already correct')
on conflict (version) do nothing;

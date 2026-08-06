-- ============================================================
-- newFirmRAG — database schema
-- Run once in the Supabase SQL Editor.
-- ============================================================

-- pgvector enables embedding similarity search (cosine distance).
create extension if not exists vector;

-- ============================================================
-- documents  — one row per embedded chunk per source file
-- ============================================================
create table if not exists public.documents
(
  id           uuid primary key default gen_random_uuid(),
  content      text not null,
  embedding    vector(1536),
  metadata     jsonb default '{}',
  access_level integer not null default 1,
  matter_id    text not null default '',
  created_at   timestamptz default now()
);

-- ============================================================
-- document_metadata  — one row per source file
-- ============================================================
create table if not exists public.document_metadata
(
  file_id      text primary key,
  file_title   text,
  url          text,
  mime_type    text,
  ingested_at  timestamptz,
  content_hash text,
  access_level integer not null default 1,
  matter_id    text not null default ''
);

-- ============================================================
-- chat_sessions  — per-user conversation state
-- ============================================================
create table if not exists public.chat_sessions
(
  session_id text primary key,
  user_id    text not null,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

-- ============================================================
-- Row level security
-- Access is granted when the caller's app.access_level >= the
-- row's access_level AND the row belongs to the caller's matter
-- (or is an open/firm-wide doc). Service role always bypasses RLS.
-- ============================================================
alter table public.documents enable row level security;
alter table public.document_metadata enable row level security;

drop policy if exists "documents_select" on public.documents;
create policy "documents_select" on public.documents
  for select
  using (
    (current_setting('app.access_level', true)::int) >= access_level
    and (
      matter_id = ''
      or matter_id = current_setting('app.matter_id', true)
    )
  );

drop policy if exists "document_metadata_select" on public.document_metadata;
create policy "document_metadata_select" on public.document_metadata
  for select
  using (
    (current_setting('app.access_level', true)::int) >= access_level
    and (
      matter_id = ''
      or matter_id = current_setting('app.matter_id', true)
    )
  );

-- ============================================================
-- delete_documents_by_file_id
-- Removes every chunk row for a given source file (idempotent
-- re-ingest). SECURITY DEFINER: allowed to run for any caller,
-- but only deletes rows that match the file_id.
-- ============================================================
create or replace function public.delete_documents_by_file_id(p_file_id text)
returns void
language sql
security definer
set search_path = public, pg_temp
as $$
  delete from public.documents
  where metadata->>'file_id' = p_file_id;
$$;

-- ============================================================
-- hybrid_search_rls
-- Reciprocal rank fusion of vector similarity + full-text search,
-- filtered by RLS (SECURITY INVOKER so row security applies).
--
-- RRF score = sum of 1.0 / (rrf_k + rank) for each list:
--   vector hits ranked by embedding <=> query_embedding
--   keyword hits ranked by full-text rank
-- Returns the top match_count rows (content + metadata).
-- ============================================================
create or replace function public.hybrid_search_rls(
  query_text      text,
  query_embedding vector,
  match_count     int,
  rrf_k           int
)
returns table (content text, metadata jsonb)
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
begin
  return query
  with vector_hits as (
    select d.content, d.metadata,
           row_number() over (order by d.embedding <=> query_embedding) as rank
    from public.documents d
    order by d.embedding <=> query_embedding
    limit match_count
  ),
  keyword_hits as (
    select d.content, d.metadata,
           row_number() over (
             order by ts_rank_cd(
               to_tsvector('english', d.content),
               plainto_tsquery('english', query_text)
             ) desc
           ) as rank
    from public.documents d
    where to_tsvector('english', d.content) @@ plainto_tsquery('english', query_text)
    limit match_count
  ),
  fused as (
    select content, metadata, (1.0 / (rrf_k + rank)) as score from vector_hits
    union all
    select content, metadata, (1.0 / (rrf_k + rank)) as score from keyword_hits
  ),
  ranked as (
    select content, metadata,
           sum(score) as total_score
    from fused
    group by content, metadata
  )
  select content, metadata
  from ranked
  order by total_score desc
  limit match_count;
end;
$$;

-- ============================================================
-- set_access_context
-- Stamps the transaction-local application context that the RLS
-- policies above read. SECURITY DEFINER so the caller can set the
-- GUCs even though they are not superusers. SET LOCAL is scoped to
-- the current transaction, which is safe with PgBouncer in
-- transaction-pooling mode.
-- ============================================================
create or replace function public.set_access_context(
  p_user_id      text,
  p_access_level int,
  p_matter_id    text
)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  set local app.user_id      = p_user_id;
  set local app.access_level = p_access_level::text;
  set local app.matter_id    = p_matter_id;
end;
$$;

-- ============================================================
-- Indexes
-- ============================================================
create index if not exists documents_embedding_idx
  on public.documents
  using ivfflat (embedding vector_cosine_ops)
  with (lists = 100);

create index if not exists documents_metadata_idx
  on public.documents
  using gin (metadata);

create index if not exists documents_fts_idx
  on public.documents
  using gin (to_tsvector('english', content));
-- ============================================================
-- Baseline migration 0000
-- ------------------------------------------------------------
-- Represents the *intended current state* of the deployment:
-- the reconciled schema that the Python code actually talks to.
--
-- This baseline:
--   * consolidates the previously split sources of truth:
--       - root schema.sql         (app.* GUCs, single matter_id)
--       - sql/schema.sql          (lawfirm.* GUCs, matter_ids[])
--       - sql/set_access_context.sql
--       - sql/match_documents_rls.sql
--       - sql/rls_policies.sql
--       - sql/functions.sql
--     into ONE versioned, ordered migration set.
--   * matches the deployed RPC signatures used by the Python app
--     (set_access_context(int, text[], boolean, uuid, text),
--      hybrid_search_rls(text, vector, int, int, text),
--      match_documents_rls(vector, int, jsonb),
--      delete_documents_by_file_id(text)).
--   * keeps backward compatibility with existing rows.
--
-- KNOWN LIMITATION (fixed in migration 0004):
--   This baseline gates reads on transaction-local GUCs
--   (lawfirm.*) that are stamped by set_access_context(). A GUC set
--   with set_config(..., true) only lives for the current transaction,
--   so it does NOT survive across independent PostgREST REST/RPC
--   calls. Migration 0004 moves authorization into JWT-claims-derived
--   database checks (auth.uid() + matter_access / user_profiles) so a
--   single retrieval request is authorized inside that same request.
--
-- Idempotent: safe to re-run.
-- ============================================================

-- ------------------------------------------------------------
-- Extensions
-- ------------------------------------------------------------
create extension if not exists vector;
create extension if not exists pgcrypto;

-- ------------------------------------------------------------
-- documents — one row per embedded chunk per source file
-- ------------------------------------------------------------
create table if not exists public.documents
(
  id           uuid primary key default gen_random_uuid(),
  content      text not null,
  embedding    vector(1536),
  metadata     jsonb not null default '{}'::jsonb,
  access_level integer not null default 1,
  matter_id    text not null default '',
  created_at   timestamptz not null default now()
);

-- ------------------------------------------------------------
-- document_metadata — one row per source file
-- ------------------------------------------------------------
create table if not exists public.document_metadata
(
  file_id              text primary key,
  file_title           text,
  url                  text,
  mime_type            text,
  ingested_at          timestamptz not null default now(),
  content_hash         text,
  drive_modified_time  text,
  doc_type             text not null default 'unknown',
  legal_entities       jsonb not null default '{}'::jsonb,
  access_level         integer not null default 1,
  matter_id            text not null default ''
);

-- ------------------------------------------------------------
-- chat_memory — LangChain PostgresChatMessageHistory rows
-- ------------------------------------------------------------
create table if not exists public.chat_memory
(
  id         bigserial primary key,
  session_id text not null,
  message    jsonb not null,
  created_at timestamptz not null default now()
);

-- ------------------------------------------------------------
-- query_feedback — thumbs up / thumbs down
-- ------------------------------------------------------------
create table if not exists public.query_feedback
(
  id             bigserial primary key,
  session_id     text not null,
  user_id        text not null,
  query          text not null,
  answer_excerpt text,
  rating         smallint not null check (rating in (-1, 1)),
  comment        text,
  created_at     timestamptz not null default now()
);

-- ------------------------------------------------------------
-- chat_sessions — per-user conversation state (legacy shape;
-- replaced/expanded by migration 0003)
-- ------------------------------------------------------------
create table if not exists public.chat_sessions
(
  session_id text primary key,
  user_id    text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- ------------------------------------------------------------
-- Indexes
-- ------------------------------------------------------------
create index if not exists documents_embedding_idx
  on public.documents using ivfflat (embedding vector_cosine_ops)
  with (lists = 100);

create index if not exists documents_metadata_idx
  on public.documents using gin (metadata);

create index if not exists documents_fts_idx
  on public.documents using gin (to_tsvector('english', content));

create index if not exists document_metadata_access_matter_idx
  on public.document_metadata (access_level, matter_id);

create index if not exists idx_doc_metadata_doc_type
  on public.document_metadata (doc_type);

create index if not exists idx_doc_metadata_legal_entities
  on public.document_metadata using gin (legal_entities);

create index if not exists chat_memory_session_idx
  on public.chat_memory (session_id);

create index if not exists idx_feedback_session
  on public.query_feedback (session_id);

create index if not exists idx_feedback_user
  on public.query_feedback (user_id);

-- ------------------------------------------------------------
-- delete_documents_by_file_id — idempotent re-ingest helper.
-- SECURITY DEFINER + granted to service_role only.
-- ------------------------------------------------------------
create or replace function public.delete_documents_by_file_id(p_file_id text)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  delete from public.documents
  where metadata->>'file_id' = p_file_id;
end;
$$;

revoke all on function public.delete_documents_by_file_id(text)
  from public, authenticated, anon;
grant execute on function public.delete_documents_by_file_id(text)
  to service_role;

-- ------------------------------------------------------------
-- match_documents_rls — vector similarity, authorization applied
-- by the function predicate AND by the RLS policy (SECURITY
-- INVOKER). Reads lawfirm.* GUCs in this baseline.
-- ------------------------------------------------------------
create or replace function public.match_documents_rls(
  query_embedding vector,
  match_count     int   default 20,
  filter          jsonb default '{}'::jsonb
)
returns table (id uuid, content text, metadata jsonb, access_level int, matter_id text, similarity float)
language sql
security invoker
set search_path = public, extensions
as $$
  select
    d.id,
    d.content,
    d.metadata,
    d.access_level,
    d.matter_id,
    1 - (d.embedding <=> query_embedding) as similarity
  from public.documents d
  where
    d.embedding is not null
    and (filter = '{}'::jsonb or d.metadata @> filter)
    and d.access_level <= coalesce(nullif(current_setting('lawfirm.access_level', true), '')::int, 0)
    and (
      coalesce(nullif(current_setting('lawfirm.view_all', true), ''), 'false')::boolean
      or d.matter_id = '' 
      or d.matter_id = any(string_to_array(coalesce(current_setting('lawfirm.matter_ids', true), ''), ','))
    )
  order by d.embedding <=> query_embedding
  limit match_count;
$$;

grant execute on function public.match_documents_rls(vector, int, jsonb)
  to authenticated, anon;

-- ------------------------------------------------------------
-- hybrid_search_rls — RRF fusion of vector + tsvector, same
-- authorization model as match_documents_rls in this baseline.
-- ------------------------------------------------------------
create or replace function public.hybrid_search_rls(
  query_text      text,
  query_embedding vector,
  match_count     int   default 20,
  rrf_k           int   default 60,
  doc_type_filter text  default null
)
returns table (id uuid, content text, metadata jsonb, access_level int, matter_id text, rrf_score float)
language plpgsql
security invoker
set search_path = public, extensions
as $$
declare
  v_access   int     := coalesce(nullif(current_setting('lawfirm.access_level', true), '')::int, 0);
  v_matters  text[]  := string_to_array(coalesce(current_setting('lawfirm.matter_ids', true), ''), ',');
  v_view_all boolean := coalesce(nullif(current_setting('lawfirm.view_all', true), ''), 'false')::boolean;
begin
  return query
  with vector_results as (
    select
      d.id,
      row_number() over (order by d.embedding <=> query_embedding) as rank
    from public.documents d
    where d.embedding is not null
      and d.access_level <= v_access
      and (v_view_all or d.matter_id = '' or d.matter_id = any(v_matters))
      and (doc_type_filter is null or d.metadata->>'doc_type' = doc_type_filter)
    order by d.embedding <=> query_embedding
    limit match_count
  ),
  keyword_results as (
    select
      d.id,
      row_number() over (
        order by ts_rank_cd(to_tsvector('english', d.content), plainto_tsquery('english', query_text)) desc
      ) as rank
    from public.documents d
    where to_tsvector('english', d.content) @@ plainto_tsquery('english', query_text)
      and d.access_level <= v_access
      and (v_view_all or d.matter_id = '' or d.matter_id = any(v_matters))
      and (doc_type_filter is null or d.metadata->>'doc_type' = doc_type_filter)
    order by ts_rank_cd(to_tsvector('english', d.content), plainto_tsquery('english', query_text)) desc
    limit match_count
  ),
  fused as (
    select
      id,
      sum(score)::float as rrf_score
    from (
      select
        id,
        1.0 / (rrf_k + rank) as score
      from vector_results
      union all
      select
        id,
        1.0 / (rrf_k + rank) as score
      from keyword_results
    ) ranked
    group by id
  )
  select
    d.id,
    d.content,
    d.metadata,
    d.access_level,
    d.matter_id,
    fused.rrf_score
  from fused
  join public.documents d on d.id = fused.id
  order by fused.rrf_score desc
  limit match_count;
end;
$$;

grant execute on function public.hybrid_search_rls(text, vector, int, int, text)
  to authenticated, anon;

-- ------------------------------------------------------------
-- match_documents — NO RLS (SECURITY DEFINER), service_role only.
-- Used by the ingest worker for internal indexing paths.
-- ------------------------------------------------------------
create or replace function public.match_documents(
  query_embedding vector,
  match_count     int   default 20,
  filter          jsonb default '{}'::jsonb
)
returns table (id uuid, content text, metadata jsonb, similarity float)
language sql
security definer
set search_path = public, extensions
as $$
  select
    d.id,
    d.content,
    d.metadata,
    1 - (d.embedding <=> query_embedding) as similarity
  from public.documents d
  where (filter = '{}'::jsonb or d.metadata @> filter)
  order by d.embedding <=> query_embedding
  limit match_count;
$$;

revoke all on function public.match_documents(vector, int, jsonb)
  from public, authenticated, anon;
grant execute on function public.match_documents(vector, int, jsonb)
  to service_role;

-- ------------------------------------------------------------
-- set_access_context — stamps transaction-local lawfirm.* GUCs.
-- SECURITY DEFINER so non-superusers can set them. Each value is
-- set with set_config(..., true) so it cannot bleed across pooled
-- connections in PgBouncer transaction mode.
--
-- DEPRECATED: migration 0004 replaces GUC-based authorization with
-- JWT-claims-derived database checks. Kept for backward
-- compatibility only; new code must not call it.
-- ------------------------------------------------------------
create or replace function public.set_access_context(
  p_access_level int,
  p_matter_ids   text[],
  p_view_all     boolean,
  p_user_id      uuid,
  p_role         text
)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  perform set_config('lawfirm.access_level', p_access_level::text,              true);
  perform set_config('lawfirm.matter_ids',   array_to_string(p_matter_ids, ','), true);
  perform set_config('lawfirm.view_all',     p_view_all::text,                   true);
  perform set_config('lawfirm.user_id',      p_user_id::text,                    true);
  perform set_config('lawfirm.role',         p_role,                             true);
end;
$$;

revoke all on function public.set_access_context(int, text[], boolean, uuid, text) from public;
grant execute on function public.set_access_context(int, text[], boolean, uuid, text)
  to authenticated, anon;

-- ------------------------------------------------------------
-- Row Level Security (legacy GUC-based shape; upgraded in 0004)
-- ------------------------------------------------------------
alter table public.documents enable row level security;
alter table public.document_metadata enable row level security;

do $$ begin
  if not exists (select 1 from pg_policies where schemaname='public' and tablename='documents' and policyname='auth can select documents') then
    execute $pol$
      create policy "auth can select documents" on public.documents for select
        to authenticated, anon
        using (
          access_level <= coalesce(nullif(current_setting('lawfirm.access_level', true), '')::int, 0)
          and (
            coalesce(nullif(current_setting('lawfirm.view_all', true), ''), 'false')::boolean
            or matter_id = ''
            or matter_id = any(string_to_array(coalesce(current_setting('lawfirm.matter_ids', true), ''), ','))
          )
        )
    $pol$;
  end if;
end $$;

do $$ begin
  if not exists (select 1 from pg_policies where schemaname='public' and tablename='documents' and policyname='service writes documents') then
    execute $pol$
      create policy "service writes documents" on public.documents for all
        to service_role using (true) with check (true)
    $pol$;
  end if;
end $$;

do $$ begin
  if not exists (select 1 from pg_policies where schemaname='public' and tablename='document_metadata' and policyname='service manages metadata') then
    execute $pol$
      create policy "service manages metadata" on public.document_metadata for all
        to service_role using (true) with check (true)
    $pol$;
  end if;
end $$;

-- ------------------------------------------------------------
-- Record migration version in schema_migrations (used by
-- scripts/apply_migrations.py and verify_schema.py).
-- ------------------------------------------------------------
create table if not exists public.schema_migrations
(
  version      text primary key,
  applied_at   timestamptz not null default now(),
  description  text not null default ''
);

insert into public.schema_migrations (version, description)
values ('20260727000000', 'baseline: consolidated documents/document_metadata/chat_memory/query_feedback/chat_sessions + GUC RLS')
on conflict (version) do nothing;
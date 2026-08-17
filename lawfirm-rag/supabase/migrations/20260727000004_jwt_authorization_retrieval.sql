-- ============================================================
-- Migration 0004 — JWT-claims authorization for retrieval
-- ------------------------------------------------------------
-- Replaces the transaction-local GUC authorization model with
-- authorization derived from the authenticated JWT (auth.uid())
-- evaluated in the SAME database request as retrieval.
--
-- Why: set_access_context() stamps lawfirm.* GUCs with
-- set_config(..., true) which is transaction-local. PostgREST runs
-- each REST/RPC request in its own transaction, so a GUC set in one
-- request does NOT survive into a later retrieval request. The old
-- model therefore could not reliably authorize retrieval.
--
-- New model (design option 1 from foundation-integrity.md):
--   * identity comes from auth.uid() (verified JWT subject);
--   * access ceilings come from user_profiles (role, access_level,
--     admin, firm_wide) — provisioned by the backend;
--   * matter grants come from matter_access — provisioned by the
--     backend;
--   * retrieval RPCs are SECURITY INVOKER and additionally apply the
--     same auth_* predicates, so function predicate + RLS policy
--     cannot disagree;
--   * set_access_context() is deprecated (kept only for backwards
--     compatibility; new code must not call it).
--
-- Firm-wide / open material:
--   * documents.matter_id = ''  → firm-wide open document; visible
--     to any user whose ceiling >= access_level.
--   * a user with matter_access row where matter_id IS NULL (or
--     user_profiles.firm_wide) → may read ALL matters at their
--     ceiling.
--   * otherwise only documents whose matter_id maps to a matter the
--     user holds a grant for.
-- ============================================================

-- ------------------------------------------------------------
-- Helper: does the caller have access to a text matter reference?
-- documents.matter_id is stored as text (matter_ref), so resolve it
-- against public.matters.
-- ------------------------------------------------------------
create or replace function public.auth_can_access_matter_ref(p_matter_ref text)
returns boolean
language sql stable security definer
set search_path = public
as $$
  select
    p_matter_ref = ''
    or public.auth_has_firm_wide()
    or exists (
      select 1
      from public.matters m
      join public.matter_access ma on ma.matter_id = m.id
      where m.matter_ref = p_matter_ref
        and m.tenant_id = public.auth_tenant_id()
        and ma.user_id = auth.uid()
    );
$$;

create or replace function public.auth_can_administer_matter_ref(p_matter_ref text)
returns boolean
language sql stable security definer
set search_path = public
as $$
  select
    p_matter_ref = ''
    and public.auth_has_firm_wide()
    or public.auth_has_firm_wide()
    or exists (
      select 1
      from public.matters m
      join public.matter_access ma on ma.matter_id = m.id
      where m.matter_ref = p_matter_ref
        and m.tenant_id = public.auth_tenant_id()
        and ma.user_id = auth.uid()
        and ma.can_administer
    );
$$;

-- ------------------------------------------------------------
-- Documents RLS — authorized in the same request via auth.uid()
-- ------------------------------------------------------------
alter table public.documents force row level security;
alter table public.document_metadata force row level security;

drop policy if exists "auth can select documents" on public.documents;
drop policy if exists "auth_uid can select documents" on public.documents;
create policy "auth_uid can select documents" on public.documents
  for select to authenticated
  using (
    access_level <= public.auth_user_access_level()
    and (
      matter_id = ''
      or public.auth_can_access_matter_ref(matter_id)
    )
  );

drop policy if exists "auth can select document_metadata" on public.document_metadata;
drop policy if exists "auth_uid can select document_metadata" on public.document_metadata;
create policy "auth_uid can select document_metadata" on public.document_metadata
  for select to authenticated
  using (
    access_level <= public.auth_user_access_level()
    and (
      matter_id = ''
      or public.auth_can_access_matter_ref(matter_id)
    )
  );

-- ------------------------------------------------------------
-- Retrieval RPCs — SECURITY INVOKER + auth_* predicates
-- ------------------------------------------------------------
-- id bigint matches documents.id's actual type on deployments where the
-- table predates this migration set (see baseline's comment on this same
-- point) — DROP first since CREATE OR REPLACE can't change return shape.
drop function if exists public.match_documents_rls(vector, int, jsonb);

create or replace function public.match_documents_rls(
  query_embedding vector,
  match_count     int   default 20,
  filter          jsonb default '{}'::jsonb
)
returns table (id bigint, content text, metadata jsonb, access_level int, matter_id text, similarity float)
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
    and d.access_level <= public.auth_user_access_level()
    and (d.matter_id = '' or public.auth_can_access_matter_ref(d.matter_id))
  order by d.embedding <=> query_embedding
  limit match_count;
$$;

grant execute on function public.match_documents_rls(vector, int, jsonb)
  to authenticated, anon;

drop function if exists public.hybrid_search_rls(text, vector, int, int, text);

create or replace function public.hybrid_search_rls(
  query_text      text,
  query_embedding vector,
  match_count     int   default 20,
  rrf_k           int   default 60,
  doc_type_filter text  default null
)
returns table (id bigint, content text, metadata jsonb, access_level int, matter_id text, rrf_score float)
language plpgsql
security invoker
set search_path = public, extensions
as $$
begin
  return query
  with vector_results as (
    select
      d.id,
      row_number() over (order by d.embedding <=> query_embedding) as rank
    from public.documents d
    where d.embedding is not null
      and d.access_level <= public.auth_user_access_level()
      and (d.matter_id = '' or public.auth_can_access_matter_ref(d.matter_id))
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
      and d.access_level <= public.auth_user_access_level()
      and (d.matter_id = '' or public.auth_can_access_matter_ref(d.matter_id))
      and (doc_type_filter is null or d.metadata->>'doc_type' = doc_type_filter)
    order by ts_rank_cd(to_tsvector('english', d.content), plainto_tsquery('english', query_text)) desc
    limit match_count
  ),
  -- `id`/`score` below are qualified with `ranked.` — this is a PL/pgSQL
  -- function with RETURNS TABLE(id bigint, ...), which implicitly declares
  -- `id` as an OUT-parameter variable in scope for the whole function body.
  -- An unqualified `id` in the CTE is ambiguous between that variable and
  -- the CTE's own column, and errors at call time (not at CREATE FUNCTION
  -- time, since PL/pgSQL body isn't parsed until first execution) — this
  -- was never actually exercised before being fixed here.
  fused as (
    select
      ranked.id,
      sum(ranked.score)::float as rrf_score
    from (
      select vector_results.id, 1.0 / (rrf_k + vector_results.rank) as score from vector_results
      union all
      select keyword_results.id, 1.0 / (rrf_k + keyword_results.rank) as score from keyword_results
    ) ranked
    group by ranked.id
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
-- Grants: the auth_* helper RPCs must be callable by the
-- authenticated role so the backend can validate upload/ingest
-- classification through the user's own PostgREST client.
-- ------------------------------------------------------------
grant execute on function public.auth_tenant_id() to authenticated;
grant execute on function public.auth_user_access_level() to authenticated;
grant execute on function public.auth_user_admin() to authenticated;
grant execute on function public.auth_has_firm_wide() to authenticated;
grant execute on function public.auth_matter_ids() to authenticated;
grant execute on function public.auth_can_access_matter(uuid) to authenticated;
grant execute on function public.auth_can_access_matter_ref(text) to authenticated;
grant execute on function public.auth_can_administer_matter(uuid) to authenticated;
grant execute on function public.auth_can_administer_matter_ref(text) to authenticated;
grant execute on function public.auth_matter_admin_level(uuid) to authenticated;

-- ------------------------------------------------------------
-- Deprecation notice for set_access_context (kept for compat).
-- No new code may call it; retrieval is authorized via auth.uid().
-- ------------------------------------------------------------
comment on function public.set_access_context(int, text[], boolean, uuid, text) is
  'DEPRECATED since migration 20260727000004. Transaction-local GUCs do not survive across PostgREST requests. Authorization is derived from auth.uid() via public.auth_user_access_level() / public.auth_can_access_matter_ref(). Kept only for backwards compatibility.';

-- ------------------------------------------------------------
-- Record migration version
-- ------------------------------------------------------------
insert into public.schema_migrations (version, description)
values ('20260727000004', 'JWT-claims authorization for retrieval (auth.uid()-derived RLS + RPC predicates; set_access_context deprecated)')
on conflict (version) do nothing;
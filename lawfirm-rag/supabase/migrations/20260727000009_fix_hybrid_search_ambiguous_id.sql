-- ================================================================
-- Migration 0009: fix ambiguous `id` in hybrid_search_rls
--
-- BUG (real, pre-existing in migrations 0000 and 0004, not
-- schema-drift-related): hybrid_search_rls is declared
-- `RETURNS TABLE(id bigint, ...)`, which makes `id` an implicit
-- OUT-parameter variable in scope for the whole PL/pgSQL function
-- body. The `fused` CTE referenced `id`/`score` unqualified, which is
-- ambiguous between that OUT-parameter variable and the CTE's own
-- column — Postgres only catches this at CALL time (PL/pgSQL function
-- bodies aren't fully parsed at CREATE FUNCTION time), so this had
-- never actually been exercised successfully until caught here.
-- Confirmed live: every hybrid_search_rls call errored with
-- "column reference \"id\" is ambiguous".
--
-- Fix: qualify every `id`/`score` reference inside the `fused` CTE.
-- See migrations 0000/0004 (patched to match, for fresh installs).
--
-- Idempotent: safe to re-run (CREATE OR REPLACE, same signature as
-- migration 0004 — no DROP needed since the return shape isn't
-- changing, only the function body).
-- ================================================================

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

insert into public.schema_migrations (version, description)
values ('20260727000009', 'fix ambiguous id reference in hybrid_search_rls CTE (was erroring on every call)')
on conflict (version) do nothing;

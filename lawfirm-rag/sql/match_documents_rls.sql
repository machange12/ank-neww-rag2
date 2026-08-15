-- ============================================================
-- DEPRECATED � see sql/DEPRECATED.md and supabase/migrations/.
-- This file is retained for reference only and MUST NOT be applied.
-- The authoritative schema is the versioned migration set under
-- supabase/migrations/ (apply via scripts/apply_migrations.py).
-- ============================================================

-- ============================================================
-- match_documents_rls.sql  —  Run third in Supabase SQL Editor
-- ============================================================

-- RLS-aware vector search (SECURITY INVOKER = RLS applies)
create or replace function public.match_documents_rls(
  query_embedding vector(1536),
  match_count     int   default 20,
  filter          jsonb default '{}'::jsonb
) returns table (id bigint, content text, metadata jsonb, similarity float)
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
    select d.id, d.content, d.metadata,
           1 - (d.embedding <=> query_embedding) as similarity
    from public.documents d
    where
      (filter = '{}'::jsonb or d.metadata @> filter)
      and d.access_level <= v_access
      and (v_view_all or d.matter_id = any(v_matters))
    order by d.embedding <=> query_embedding
    limit match_count;
end;
$$;

grant execute on function public.match_documents_rls(vector, int, jsonb)
  to authenticated, anon;

-- Plain insert-path search (no RLS) — service role only
create or replace function public.match_documents(
  query_embedding vector(1536),
  match_count     int   default 20,
  filter          jsonb default '{}'::jsonb
) returns table (id bigint, content text, metadata jsonb, similarity float)
language plpgsql
security definer
set search_path = public, extensions
as $$
begin
  return query
    select d.id, d.content, d.metadata,
           1 - (d.embedding <=> query_embedding) as similarity
    from public.documents d
    where (filter = '{}'::jsonb or d.metadata @> filter)
    order by d.embedding <=> query_embedding
    limit match_count;
end;
$$;

revoke all on function public.match_documents(vector, int, jsonb) from public, authenticated, anon;
grant  execute on function public.match_documents(vector, int, jsonb) to service_role;

-- Delete helper used by ingest worker (idempotent re-ingest)
create or replace function public.delete_documents_by_file_id(p_file_id text)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  delete from public.documents where metadata->>'file_id' = p_file_id;
end;
$$;

revoke all on function public.delete_documents_by_file_id(text) from public, authenticated, anon;
grant  execute on function public.delete_documents_by_file_id(text) to service_role;

-- Verify:
-- select proname, prosecdef from pg_proc
--  where proname in ('match_documents_rls','match_documents','set_access_context','delete_documents_by_file_id');
-- prosecdef: f for match_documents_rls, t for the others

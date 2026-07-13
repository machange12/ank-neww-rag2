-- =============================================================
-- match_documents_rls function (used by SupabaseVectorStore tool)
-- SECURITY INVOKER => RLS policies on documents apply
-- Reads GUCs set by set_access_context
-- =============================================================

create or replace function public.match_documents_rls(
  query_embedding vector(1536),
  match_count     int default 20,
  filter          jsonb default '{}'::jsonb
)
returns table (
  id         bigint,
  content    text,
  metadata   jsonb,
  similarity float
)
language plpgsql
security invoker
set search_path = public, extensions
as $$
declare
  v_access  int     := coalesce(nullif(current_setting('lawfirm.access_level', true), '')::int, 0);
  v_matters text[]  := string_to_array(coalesce(current_setting('lawfirm.matter_ids', true), ''), ',');
  v_view_all boolean := coalesce(nullif(current_setting('lawfirm.view_all', true), ''), 'false')::boolean;
begin
  return query
    select d.id,
           d.content,
           d.metadata,
           1 - (d.embedding <=> query_embedding) as similarity
      from public.documents d
     where (filter = '{}'::jsonb or d.metadata @> filter)
       and d.access_level <= v_access
       and (v_view_all or d.matter_id = any(v_matters))
     order by d.embedding <=> query_embedding
     limit match_count;
end;
$$;

grant execute on function public.match_documents_rls(
  vector, int, jsonb
) to authenticated, anon;

-- Plain text insert used by ingest worker
create or replace function public.match_documents(
  query_embedding vector(1536),
  match_count     int default 20,
  filter          jsonb default '{}'::jsonb
)
returns table (
  id         bigint,
  content    text,
  metadata   jsonb,
  similarity float
)
language sql
security invoker
set search_path = public, extensions
as $$
  select d.id,
         d.content,
         d.metadata,
         1 - (d.embedding <=> query_embedding) as similarity
    from public.documents d
   where (filter = '{}'::jsonb or d.metadata @> filter)
   order by d.embedding <=> query_embedding
   limit match_count;
$$;

grant execute on function public.match_documents(vector, int, jsonb)
  to service_role;

-- Cleanup helper used by ingest.store.delete_old_rows_for_file
create or replace function public.delete_documents_by_file_id(p_file_id text)
returns int
language sql
security definer
set search_path = public, pg_temp
as $$
  with deleted as (
    delete from public.documents
     where metadata->>'file_id' = p_file_id
     returning 1
  )
  select count(*)::int from deleted;
$$;

revoke all on function public.delete_documents_by_file_id(text) from public;
grant execute on function public.delete_documents_by_file_id(text) to service_role;

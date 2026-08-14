-- ============================================================
-- functions.sql  -  RLS-aware search RPCs + ingest delete helper
-- ============================================================

create extension if not exists vector;

create index if not exists documents_embedding_ivfflat_idx
  on public.documents
  using ivfflat (embedding vector_cosine_ops)
  with (lists = 100);

create index if not exists documents_content_tsv_gin_idx
  on public.documents
  using gin (to_tsvector('english', content));

create or replace function public.match_documents_rls(
  query_embedding vector,
  match_count int default 20,
  filter jsonb default '{}'::jsonb
) returns table (
  id bigint,
  content text,
  metadata jsonb,
  access_level int,
  matter_id text,
  similarity float
)
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
  order by d.embedding <=> query_embedding
  limit match_count;
$$;

grant execute on function public.match_documents_rls(vector, int, jsonb)
  to authenticated, anon;

create or replace function public.hybrid_search_rls(
  query_text text,
  query_embedding vector,
  match_count int default 20,
  rrf_k int default 60,
  doc_type_filter text default null
) returns table (
  id bigint,
  content text,
  metadata jsonb,
  access_level int,
  matter_id text,
  rrf_score float
)
language sql
security invoker
set search_path = public, extensions
as $$
  with vector_results as (
    select
      d.id,
      row_number() over (order by d.embedding <=> query_embedding) as rank
    from public.documents d
    where d.embedding is not null
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
$$;

grant execute on function public.hybrid_search_rls(text, vector, int, int, text)
  to authenticated, anon;

create or replace function public.delete_documents_by_file_id(p_file_id text)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  delete from public.documents
  where metadata->>'file_id' = p_file_id;
end;
$$;

alter function public.delete_documents_by_file_id(text) owner to postgres;
revoke all on function public.delete_documents_by_file_id(text)
  from public, authenticated, anon;
grant execute on function public.delete_documents_by_file_id(text)
  to service_role;

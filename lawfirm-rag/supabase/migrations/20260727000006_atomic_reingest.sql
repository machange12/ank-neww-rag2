-- ================================================================
-- Migration 0006: atomic re-ingest helper
--
-- KNOWN LIMITATION (baseline): upsert_file() deleted a file's old
-- vector rows via delete_documents_by_file_id() BEFORE inserting the
-- newly embedded rows. If the embed/insert step then failed (network,
-- LLM error, etc.) the file was left with zero rows — unsearchable
-- until the next successful re-ingest.
--
-- Fix: add a cutoff-scoped delete so the application can insert the
-- new rows FIRST, then delete only the rows that existed before the
-- new insert (by created_at). A failed insert now leaves the old rows
-- in place instead of leaving the file with no rows at all.
--
-- Idempotent: safe to re-run.
-- ================================================================

-- Prerequisite: this function filters on created_at, which the baseline
-- `create table if not exists` never adds to a `documents` table that
-- already existed before these migrations were applied (as found on a live
-- deployment that predates this migration set — IF NOT EXISTS means it's a
-- no-op against an existing table, so a column baseline "declares" isn't
-- guaranteed to actually be there). Safe/idempotent either way.
alter table public.documents
  add column if not exists created_at timestamptz not null default now();

create or replace function public.delete_documents_by_file_id_before(
  p_file_id text,
  p_before  timestamptz
)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  delete from public.documents
  where metadata->>'file_id' = p_file_id
    and created_at < p_before;
end;
$$;

revoke all on function public.delete_documents_by_file_id_before(text, timestamptz)
  from public, authenticated, anon;
grant execute on function public.delete_documents_by_file_id_before(text, timestamptz)
  to service_role;

-- ------------------------------------------------------------
-- Record migration version
-- ------------------------------------------------------------
insert into public.schema_migrations (version, description)
values ('20260727000006', 'atomic re-ingest: delete_documents_by_file_id_before')
on conflict (version) do nothing;

-- =============================================================
-- RLS policies for documents
--   1. ENABLE + FORCE RLS so policies apply even to table owner
--   2. anon/authenticated SELECT policed by current_setting()
--   3. service_role bypasses (used by ingest/cleanup only)
-- =============================================================

alter table public.documents enable row level security;
alter table public.documents force row level security;

drop policy if exists "auth can select documents" on public.documents;
create policy "auth can select documents"
  on public.documents
  for select
  to authenticated, anon
  using (
        access_level
          <= coalesce(nullif(current_setting('lawfirm.access_level', true), '')::int, 0)
    and (
          coalesce(nullif(current_setting('lawfirm.view_all', true), ''), 'false')::boolean
          or matter_id = any(
                string_to_array(
                  coalesce(current_setting('lawfirm.matter_ids', true), ''),
                  ','
                )
              )
        )
  );

drop policy if exists "service writes documents" on public.documents;
create policy "service writes documents"
  on public.documents
  for all
  to service_role
  using (true)
  with check (true);

alter table public.document_metadata enable row level security;
alter table public.document_metadata force row level security;

drop policy if exists "service manages document_metadata" on public.document_metadata;
create policy "service manages document_metadata"
  on public.document_metadata
  for all
  to service_role
  using (true)
  with check (true);

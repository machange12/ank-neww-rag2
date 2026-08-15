-- ============================================================
-- DEPRECATED — see sql/DEPRECATED.md and supabase/migrations/.
-- This file is retained for reference only and MUST NOT be applied.
-- The authoritative schema is the versioned migration set under
-- supabase/migrations/ (apply via scripts/apply_migrations.py).
-- ============================================================

-- ============================================================
-- rls_policies.sql  â€”  Run fourth in Supabase SQL Editor
-- ============================================================

-- documents
alter table public.documents enable row level security;
alter table public.documents force row level security;

do $$ begin
  if not exists (select 1 from pg_policies where tablename='documents' and policyname='auth can select documents') then
    execute $pol$
      create policy "auth can select documents" on public.documents for select
        to authenticated, anon
        using (
          access_level <= coalesce(nullif(current_setting('lawfirm.access_level', true), '')::int, 0)
          and (
            coalesce(nullif(current_setting('lawfirm.view_all', true), ''), 'false')::boolean
            or matter_id = any(string_to_array(coalesce(current_setting('lawfirm.matter_ids', true), ''), ','))
          )
        )
    $pol$;
  end if;
end $$;

do $$ begin
  if not exists (select 1 from pg_policies where tablename='documents' and policyname='service writes documents') then
    execute $pol$
      create policy "service writes documents" on public.documents for all
        to service_role using (true) with check (true)
    $pol$;
  end if;
end $$;

-- document_metadata (service role writes; authenticated reads own rows via join)
alter table public.document_metadata enable row level security;
alter table public.document_metadata force row level security;

do $$ begin
  if not exists (select 1 from pg_policies where tablename='document_metadata' and policyname='service manages metadata') then
    execute $pol$
      create policy "service manages metadata" on public.document_metadata for all
        to service_role using (true) with check (true)
    $pol$;
  end if;
end $$;

-- Verify:
-- select relname, relrowsecurity, relforcerowsecurity
--   from pg_class where relname in ('documents','document_metadata');
-- Both columns should be t (true).

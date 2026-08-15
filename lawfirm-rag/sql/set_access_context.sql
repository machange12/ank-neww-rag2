-- ============================================================
-- DEPRECATED — see sql/DEPRECATED.md and supabase/migrations/.
-- This file is retained for reference only and MUST NOT be applied.
-- The authoritative schema is the versioned migration set under
-- supabase/migrations/ (apply via scripts/apply_migrations.py).
-- ============================================================

-- ============================================================
-- set_access_context.sql  â€”  Run second in Supabase SQL Editor
-- ============================================================

create or replace function public.set_access_context(
  p_access_level int,
  p_matter_ids   text[],
  p_view_all     boolean,
  p_user_id      uuid,
  p_role         text
) returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  perform set_config('request.jwt.claims', json_build_object(
    'sub',  p_user_id,
    'role', 'authenticated',
    'app_metadata', json_build_object(
      'role',         p_role,
      'access_level', p_access_level,
      'matter_ids',   to_jsonb(p_matter_ids)
    )
  )::text, true);

  -- true = transaction-local; safe with PgBouncer in transaction mode
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

-- Verify:
-- select set_access_context(3, array['M-2024-118'], false, auth.uid(), 'senior_associate');
-- select current_setting('lawfirm.access_level', true);  -- should return '3'

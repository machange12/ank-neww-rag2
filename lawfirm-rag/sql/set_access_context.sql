-- =============================================================
-- set_access_context RPC (called by Python rls.access_context)
-- Stamps the current transaction with the user's role / matter IDs
-- =============================================================

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
  perform set_config(
    'lawfirm.access_level',
    coalesce(p_access_level::text, ''),
    true
  );
  perform set_config(
    'lawfirm.matter_ids',
    array_to_string(coalesce(p_matter_ids, '{}'), ','),
    true
  );
  perform set_config(
    'lawfirm.view_all',
    coalesce(p_view_all::text, 'false'),
    true
  );
  perform set_config(
    'lawfirm.user_id',
    coalesce(p_user_id::text, ''),
    true
  );
  perform set_config(
    'lawfirm.role',
    coalesce(p_role, ''),
    true
  );
end;
$$;

revoke all on function public.set_access_context(
  int, text[], boolean, uuid, text
) from public;
grant execute on function public.set_access_context(
  int, text[], boolean, uuid, text
) to authenticated, anon;

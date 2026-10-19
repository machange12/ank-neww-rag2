-- ============================================================
-- Migration 0014 — matter-scoped draft sharing ("Draft Studio")
-- ------------------------------------------------------------
-- Drafts are owner-only by default (migration 0012). This migration adds an
-- EXPLICIT, READ-ONLY share: the owner can publish a draft to a single matter
-- team, and every user who holds a matter_access grant for that matter may then
-- READ the draft (and its sections / versions / last check). Nothing else
-- changes — writes stay owner-only, so a shared draft can never be edited,
-- regenerated, versioned or re-shared by a non-owner.
--
-- Model:
--   * drafts.visibility — 'private' (default) | 'matter'. A row is only ever
--     visible to a matter when the owner has explicitly set 'matter' AND the
--     draft carries a non-empty matter_id (an empty matter_id resolves to
--     "everyone" in auth_can_access_matter_ref, so it is explicitly excluded).
--   * child tables (draft_sections / draft_versions / draft_checks) gain a
--     matching parent-inherited SELECT policy so a matter member can open a
--     shared draft; their INSERT/UPDATE/DELETE policies are untouched.
--   * firm-wide roles already access every matter via auth_can_access_matter_ref
--     (auth_has_firm_wide), which is the intended behaviour.
--
-- Idempotent: safe to re-run.
-- ============================================================

-- ------------------------------------------------------------
-- Visibility flag (explicit opt-in; default private)
-- ------------------------------------------------------------
alter table public.drafts
  add column if not exists visibility text not null default 'private';

alter table public.drafts drop constraint if exists drafts_visibility_check;
alter table public.drafts add constraint drafts_visibility_check
  check (visibility in ('private', 'matter'));

create index if not exists drafts_visibility_idx
  on public.drafts (visibility, matter_id);

-- ------------------------------------------------------------
-- drafts: owner OR (explicitly shared to a matter the caller can access)
-- ------------------------------------------------------------
drop policy if exists "drafts_select_own" on public.drafts;
create policy "drafts_select_own" on public.drafts
  for select to authenticated
  using (
    user_id_uuid = auth.uid()
    or (
      visibility = 'matter'
      and matter_id <> ''
      and public.auth_can_access_matter_ref(matter_id)
    )
  );

-- Writes remain owner-only. Re-assert them so a shared draft can never be
-- mutated by a matter member even if an earlier policy name changed.
drop policy if exists "drafts_insert_own" on public.drafts;
create policy "drafts_insert_own" on public.drafts
  for insert to authenticated
  with check (user_id_uuid = auth.uid());

drop policy if exists "drafts_update_own" on public.drafts;
create policy "drafts_update_own" on public.drafts
  for update to authenticated
  using (user_id_uuid = auth.uid())
  with check (user_id_uuid = auth.uid());

drop policy if exists "drafts_delete_own" on public.drafts;
create policy "drafts_delete_own" on public.drafts
  for delete to authenticated
  using (user_id_uuid = auth.uid());

-- ------------------------------------------------------------
-- draft_sections: add parent-inherited READ for matter-shared drafts.
-- (!= the insert/update/delete policies from 0012, which stay owner-only.)
-- ------------------------------------------------------------
drop policy if exists "draft_sections_select_shared" on public.draft_sections;
create policy "draft_sections_select_shared" on public.draft_sections
  for select to authenticated
  using (exists (
    select 1 from public.drafts d
    where d.id = draft_sections.draft_id
      and d.visibility = 'matter'
      and d.matter_id <> ''
      and public.auth_can_access_matter_ref(d.matter_id)
  ));

-- ------------------------------------------------------------
-- draft_versions: parent-inherited READ for matter-shared drafts.
-- ------------------------------------------------------------
drop policy if exists "draft_versions_select_shared" on public.draft_versions;
create policy "draft_versions_select_shared" on public.draft_versions
  for select to authenticated
  using (exists (
    select 1 from public.drafts d
    where d.id = draft_versions.draft_id
      and d.visibility = 'matter'
      and d.matter_id <> ''
      and public.auth_can_access_matter_ref(d.matter_id)
  ));

-- ------------------------------------------------------------
-- draft_checks: parent-inherited READ for matter-shared drafts.
-- ------------------------------------------------------------
drop policy if exists "draft_checks_select_shared" on public.draft_checks;
create policy "draft_checks_select_shared" on public.draft_checks
  for select to authenticated
  using (exists (
    select 1 from public.drafts d
    where d.id = draft_checks.draft_id
      and d.visibility = 'matter'
      and d.matter_id <> ''
      and public.auth_can_access_matter_ref(d.matter_id)
  ));

-- ------------------------------------------------------------
-- Record migration version
-- ------------------------------------------------------------
insert into public.schema_migrations (version, description)
values ('20261013000014', 'matter-scoped draft sharing — drafts.visibility + read-only RLS for shared drafts (sections/versions/checks)')
on conflict (version) do nothing;

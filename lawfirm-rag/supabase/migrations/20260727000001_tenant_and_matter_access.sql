-- ============================================================
-- Migration 0001 — tenant + matter authorization model
-- ------------------------------------------------------------
-- Introduces the explicit authorization data model that
-- replaces GUC-based access control (completed in migration 0004):
--
--   tenants        — one row per firm/tenant (the product is
--                    multi-tenant capable; the default deployment
--                    has a single firm tenant).
--   matters        — one row per matter (legal engagement).
--   user_profiles  — the DB-side source of truth for a user's role,
--                    access-level ceiling, firm-wide flag and admin
--                    flag. Provisioned by the backend (never by the
--                    client).
--   matter_access  — user-to-matter authorization junction.
--                    * multiple permitted matters per user
--                    * a row with matter_id IS NULL grants firm-wide
--                      (read-all) access at that access_level
--                    * can_administer marks who may ingest/upload
--                      into that matter
--
-- Helper SECURITY DEFINER functions (derive from auth.uid() so a
-- single retrieval request is authorized inside that same request):
--   auth_tenant_id()            -> caller's tenant id
--   auth_user_access_level()    -> caller's ceiling
--   auth_user_admin()           -> caller's explicit admin flag
--   auth_has_firm_wide()        -> firm-wide read grant present
--   auth_matter_ids()           -> matter ids caller may READ
--   auth_can_access_matter(uuid)-> bool for a given matter id
--   auth_can_administer_matter(uuid) -> bool for upload/ingest
--   auth_matter_admin_level(uuid)    -> access level ceiling for
--                                  uploads into that matter
--
-- RLS is enabled on user_profiles / matter_access so the app can
-- read a user's OWN grants through the user's own PostgREST client
-- (no service-role needed for user-facing paths).
-- ============================================================

-- ------------------------------------------------------------
-- tenants
-- ------------------------------------------------------------
create table if not exists public.tenants
(
  id         uuid primary key default gen_random_uuid(),
  name       text not null,
  created_at timestamptz not null default now()
);

-- The default firm tenant used by a single-firm deployment.
insert into public.tenants (id, name)
values ('00000000-0000-0000-0000-000000000001', 'Anjarwalla & Khanna Advocates')
on conflict (id) do nothing;

-- ------------------------------------------------------------
-- matters
-- ------------------------------------------------------------
create table if not exists public.matters
(
  id          uuid primary key default gen_random_uuid(),
  tenant_id   uuid not null references public.tenants(id) on delete cascade,
  matter_ref  text not null,
  name        text,
  created_at  timestamptz not null default now(),
  unique (tenant_id, matter_ref)
);

-- ------------------------------------------------------------
-- user_profiles — DB source of truth for role + ceilings
-- ------------------------------------------------------------
create table if not exists public.user_profiles
(
  user_id      uuid primary key,
  tenant_id    uuid not null references public.tenants(id) on delete cascade,
  role         text not null,
  access_level integer not null default 1,
  firm_wide    boolean not null default false,
  admin        boolean not null default false,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);

-- ------------------------------------------------------------
-- matter_access — user-to-matter grants
-- ------------------------------------------------------------
create table if not exists public.matter_access
(
  id             uuid primary key default gen_random_uuid(),
  tenant_id      uuid not null references public.tenants(id) on delete cascade,
  user_id        uuid not null references auth.users(id) on delete cascade,
  matter_id      uuid references public.matters(id) on delete cascade,
  access_level   integer not null default 1,
  can_administer boolean not null default false,
  created_at     timestamptz not null default now(),
  unique (user_id, matter_id)
);
create index if not exists matter_access_user_idx  on public.matter_access (user_id);
create index if not exists matter_access_matter_idx on public.matter_access (matter_id);

-- ------------------------------------------------------------
-- helper functions — all derive identity from auth.uid()
-- ------------------------------------------------------------
create or replace function public.auth_tenant_id()
returns uuid
language sql stable security definer
set search_path = public
as $$
  select tenant_id
  from public.user_profiles
  where user_id = auth.uid();
$$;

create or replace function public.auth_user_access_level()
returns integer
language sql stable security definer
set search_path = public
as $$
  select coalesce(max(access_level), 0)
  from public.user_profiles
  where user_id = auth.uid();
$$;

create or replace function public.auth_user_admin()
returns boolean
language sql stable security definer
set search_path = public
as $$
  select coalesce(bool_or(admin), false)
  from public.user_profiles
  where user_id = auth.uid();
$$;

create or replace function public.auth_has_firm_wide()
returns boolean
language sql stable security definer
set search_path = public
as $$
  select
    coalesce(bool_or(firm_wide), false)
    or exists (
      select 1
      from public.matter_access ma
      where ma.user_id = auth.uid()
        and ma.matter_id is null
    )
  from public.user_profiles
  where user_id = auth.uid();
$$;

create or replace function public.auth_matter_ids()
returns uuid[]
language sql stable security definer
set search_path = public
as $$
  select case
    when public.auth_has_firm_wide() then array(
      select id from public.matters where tenant_id = public.auth_tenant_id()
    )
    else array(
      select ma.matter_id
      from public.matter_access ma
      where ma.user_id = auth.uid()
        and ma.matter_id is not null
    )
  end;
$$;

create or replace function public.auth_can_access_matter(p_matter_id uuid)
returns boolean
language sql stable security definer
set search_path = public
as $$
  select
    public.auth_has_firm_wide()
    or exists (
      select 1
      from public.matter_access ma
      where ma.user_id = auth.uid()
        and ma.matter_id = p_matter_id
    );
$$;

create or replace function public.auth_can_administer_matter(p_matter_id uuid)
returns boolean
language sql stable security definer
set search_path = public
as $$
  select
    public.auth_has_firm_wide()
    or exists (
      select 1
      from public.matter_access ma
      where ma.user_id = auth.uid()
        and ma.matter_id = p_matter_id
        and ma.can_administer
    );
$$;

create or replace function public.auth_matter_admin_level(p_matter_id uuid)
returns integer
language sql stable security definer
set search_path = public
as $$
  select coalesce(max(access_level), 0)
  from public.matter_access ma
  where ma.user_id = auth.uid()
    and ma.matter_id = p_matter_id
    and ma.can_administer;
$$;

-- ------------------------------------------------------------
-- RLS on the new authorization tables
-- ------------------------------------------------------------
alter table public.user_profiles enable row level security;
alter table public.matter_access enable row level security;

drop policy if exists "user_profiles_select_own" on public.user_profiles;
create policy "user_profiles_select_own" on public.user_profiles
  for select to authenticated
  using (user_id = auth.uid());

drop policy if exists "matter_access_select_own" on public.matter_access;
create policy "matter_access_select_own" on public.matter_access
  for select to authenticated
  using (user_id = auth.uid());

drop policy if exists "matter_access_service_all" on public.matter_access;
create policy "matter_access_service_all" on public.matter_access
  for all to service_role using (true) with check (true);

drop policy if exists "user_profiles_service_all" on public.user_profiles;
create policy "user_profiles_service_all" on public.user_profiles
  for all to service_role using (true) with check (true);

drop policy if exists "matters_service_all" on public.matters;
create policy "matters_service_all" on public.matters
  for all to service_role using (true) with check (true);

drop policy if exists "tenants_service_all" on public.tenants;
create policy "tenants_service_all" on public.tenants
  for all to service_role using (true) with check (true);

-- ------------------------------------------------------------
-- Record migration version
-- ------------------------------------------------------------
insert into public.schema_migrations (version, description)
values ('20260727000001', 'tenant + matter authorization model (tenants, matters, user_profiles, matter_access) + auth helpers')
on conflict (version) do nothing;
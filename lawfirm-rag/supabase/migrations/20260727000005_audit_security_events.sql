-- ============================================================
-- Migration 0005 — audit & security-event log
-- ------------------------------------------------------------
-- Appends the security-event audit trail used by the backend to
-- record denied / suspicious operations (failed auth, forbidden
-- upload classification, rate-limit denials, admin denials).
--
-- Policy on telemetry: never persist raw privileged document text
-- in these rows. Only identifiers, actor, action, outcome and a
-- short detail string.
-- ============================================================

create table if not exists public.security_events
(
  id           uuid primary key default gen_random_uuid(),
  tenant_id    uuid references public.tenants(id),
  user_id      uuid,
  actor_email  text,
  event_type   text not null,
  action       text not null,
  outcome      text not null default 'unknown',
  detail       text,
  ip_address   text,
  created_at   timestamptz not null default now()
);

-- Additive, in case security_events already existed under a different
-- (bespoke, pre-migration-tracking) shape — found on a live deployment
-- with `actor text` instead of `user_id uuid`, and missing
-- outcome/actor_email/detail/ip_address entirely. audit/events.py writes
-- user_id/outcome/actor_email/detail/ip_address on every event; without
-- these columns every audit write was silently failing (caught by
-- record_event's best-effort try/except).
alter table public.security_events add column if not exists tenant_id uuid references public.tenants(id);
alter table public.security_events add column if not exists user_id uuid;
alter table public.security_events add column if not exists actor_email text;
alter table public.security_events add column if not exists outcome text not null default 'unknown';
alter table public.security_events add column if not exists detail text;
alter table public.security_events add column if not exists ip_address text;

create index if not exists security_events_user_idx on public.security_events (user_id);
create index if not exists security_events_type_idx on public.security_events (event_type, created_at);

alter table public.security_events enable row level security;

drop policy if exists "security_events_service_all" on public.security_events;
create policy "security_events_service_all" on public.security_events
  for all to service_role using (true) with check (true);

drop policy if exists "security_events_admin_read" on public.security_events;
create policy "security_events_admin_read" on public.security_events
  for select to authenticated
  using (public.auth_user_admin());

-- ------------------------------------------------------------
-- Record migration version
-- ------------------------------------------------------------
insert into public.schema_migrations (version, description)
values ('20260727000005', 'audit & security-event log (security_events)')
on conflict (version) do nothing;
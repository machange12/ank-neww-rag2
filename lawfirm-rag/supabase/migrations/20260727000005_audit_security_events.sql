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
  outcome      text not null,
  detail       text,
  ip_address   text,
  created_at   timestamptz not null default now()
);
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
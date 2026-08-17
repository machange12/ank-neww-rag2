-- ============================================================
-- Migration 0003 — secure chat-session & feedback ownership
-- ------------------------------------------------------------
-- * Promotes chat_sessions to a proper model: uuid PK, tenant_id,
--   user_id, optional matter_id, lifecycle metadata.
-- * Adds tenant_id / user_id / session_id ownership columns to
--   chat_memory and query_feedback.
-- * Enables RLS so a user can only read/write/delete their OWN
--   conversations and feedback.
-- * Backfill note (IMPORTANT): legacy session keys are stored as
--   "<user_id>__<client_session_id>" text. user_id is backfilled by
--   parsing that prefix. New sessions use a generated UUID string
--   as the session key; ownership always comes from the DB row,
--   never from parsing the key.
-- ============================================================

-- ------------------------------------------------------------
-- chat_sessions — proper ownership model
-- ------------------------------------------------------------
alter table public.chat_sessions
  add column if not exists id uuid not null default gen_random_uuid();
-- Required before chat_memory.session_uuid (below) can reference this
-- column: a bare ADD COLUMN gives it no uniqueness, and Postgres refuses
-- a foreign key against a column with no unique constraint/index. This
-- was missing outright — would have failed the same way on a from-scratch
-- baseline+0003 apply, not just against a pre-existing live schema.
create unique index if not exists chat_sessions_id_uq on public.chat_sessions (id);
alter table public.chat_sessions
  add column if not exists tenant_id uuid references public.tenants(id);
alter table public.chat_sessions
  add column if not exists user_id_uuid uuid;
alter table public.chat_sessions
  add column if not exists matter_id uuid references public.matters(id);
alter table public.chat_sessions
  add column if not exists status text not null default 'active';
alter table public.chat_sessions
  add column if not exists last_activity_at timestamptz not null default now();
alter table public.chat_sessions
  add column if not exists retention_days integer not null default 365;

-- Backfill user_id_uuid from the legacy "user_id__session" key prefix.
update public.chat_sessions
set user_id_uuid = nullif(split_part(session_id, '__', 1), '')::uuid
where user_id_uuid is null
  and split_part(session_id, '__', 1) ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$';

-- The authoritative ownership column is user_id_uuid (uuid). Kept
-- separate from the legacy text column; new app code writes to
-- user_id_uuid. The text session_id remains the external session key.

-- Keep text session_id unique for backward-compatible lookups.
alter table public.chat_sessions
  alter column session_id set not null;
create unique index if not exists chat_sessions_session_id_uq on public.chat_sessions (session_id);

create index if not exists chat_sessions_user_idx on public.chat_sessions (user_id_uuid);

-- ------------------------------------------------------------
-- chat_memory — ownership columns + backfill
-- ------------------------------------------------------------
alter table public.chat_memory
  add column if not exists tenant_id uuid references public.tenants(id);
alter table public.chat_memory
  add column if not exists user_id uuid;
alter table public.chat_memory
  add column if not exists session_uuid uuid references public.chat_sessions(id);

-- Backfill user_id from the legacy session key prefix.
update public.chat_memory
set user_id = nullif(split_part(session_id, '__', 1), '')::uuid
where user_id is null
  and split_part(session_id, '__', 1) ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$';

create index if not exists chat_memory_user_idx on public.chat_memory (user_id);
create index if not exists chat_memory_session_uuid_idx on public.chat_memory (session_uuid);

-- ------------------------------------------------------------
-- query_feedback — ownership columns + backfill
-- ------------------------------------------------------------
alter table public.query_feedback
  add column if not exists tenant_id uuid references public.tenants(id);
alter table public.query_feedback
  add column if not exists user_id_uuid uuid;

-- Type-aware: baseline declares query_feedback.user_id as text, but on a
-- deployment where this table predates this migration set, user_id may
-- already be uuid (found live) — a text regex/cast against an already-uuid
-- column is a type error, not just a no-op.
do $$
declare
  user_id_type text;
begin
  select data_type into user_id_type
  from information_schema.columns
  where table_schema = 'public' and table_name = 'query_feedback' and column_name = 'user_id';

  if user_id_type = 'uuid' then
    update public.query_feedback
    set user_id_uuid = user_id
    where user_id_uuid is null;
  else
    update public.query_feedback
    set user_id_uuid = nullif(user_id, '')::uuid
    where user_id_uuid is null
      and user_id ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$';
  end if;
end $$;

create index if not exists idx_feedback_user_uuid on public.query_feedback (user_id_uuid);

-- ------------------------------------------------------------
-- RLS on chat_sessions / chat_memory / query_feedback
-- ------------------------------------------------------------
alter table public.chat_sessions enable row level security;
alter table public.chat_memory enable row level security;
alter table public.query_feedback enable row level security;

drop policy if exists "chat_sessions_select_own" on public.chat_sessions;
create policy "chat_sessions_select_own" on public.chat_sessions
  for select to authenticated
  using (user_id_uuid = auth.uid());

drop policy if exists "chat_sessions_insert_own" on public.chat_sessions;
create policy "chat_sessions_insert_own" on public.chat_sessions
  for insert to authenticated
  with check (user_id_uuid = auth.uid());

drop policy if exists "chat_sessions_update_own" on public.chat_sessions;
create policy "chat_sessions_update_own" on public.chat_sessions
  for update to authenticated
  using (user_id_uuid = auth.uid())
  with check (user_id_uuid = auth.uid());

drop policy if exists "chat_sessions_delete_own" on public.chat_sessions;
create policy "chat_sessions_delete_own" on public.chat_sessions
  for delete to authenticated
  using (user_id_uuid = auth.uid());

drop policy if exists "chat_sessions_service_all" on public.chat_sessions;
create policy "chat_sessions_service_all" on public.chat_sessions
  for all to service_role using (true) with check (true);

-- chat_memory: own-history reads + writes only.
drop policy if exists "chat_memory_select_own" on public.chat_memory;
create policy "chat_memory_select_own" on public.chat_memory
  for select to authenticated
  using (user_id = auth.uid());

drop policy if exists "chat_memory_insert_own" on public.chat_memory;
create policy "chat_memory_insert_own" on public.chat_memory
  for insert to authenticated
  with check (user_id = auth.uid());

drop policy if exists "chat_memory_delete_own" on public.chat_memory;
create policy "chat_memory_delete_own" on public.chat_memory
  for delete to authenticated
  using (user_id = auth.uid());

drop policy if exists "chat_memory_service_all" on public.chat_memory;
create policy "chat_memory_service_all" on public.chat_memory
  for all to service_role using (true) with check (true);

-- query_feedback: only the owning user can read; create is
-- constrained to the caller's own user_id (verified from the JWT
-- in the backend, not from the request body).
drop policy if exists "query_feedback_select_own" on public.query_feedback;
create policy "query_feedback_select_own" on public.query_feedback
  for select to authenticated
  using (coalesce(user_id_uuid, user_id::uuid) = auth.uid());

drop policy if exists "query_feedback_insert_own" on public.query_feedback;
create policy "query_feedback_insert_own" on public.query_feedback
  for insert to authenticated
  with check (coalesce(user_id_uuid, user_id::uuid) = auth.uid());

drop policy if exists "query_feedback_service_all" on public.query_feedback;
create policy "query_feedback_service_all" on public.query_feedback
  for all to service_role using (true) with check (true);

-- ------------------------------------------------------------
-- Retention note
-- ------------------------------------------------------------
-- No silent data loss: legacy chat history is preserved and
-- backfilled (see the UPDATE statements above). A documented
-- retention job should delete sessions older than
-- chat_sessions.retention_days (see docs/operations/migrations-and-rollbacks.md).
-- ============================================================

-- ------------------------------------------------------------
-- Record migration version
-- ------------------------------------------------------------
insert into public.schema_migrations (version, description)
values ('20260727000003', 'chat session & feedback ownership (chat_sessions uuid model, chat_memory/query_feedback ownership + RLS, legacy backfill)')
on conflict (version) do nothing;
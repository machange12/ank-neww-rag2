-- ============================================================
-- Migration 0013 — playbook check results ("Draft Studio")
-- ------------------------------------------------------------
-- Adds draft_checks: one row per playbook review of a draft. Each row keeps
-- the structured flags (rule position/severity + ok|breach|unclear verdict
-- per section) and a small summary, so the reviewer's last check survives a
-- reload and the firm retains an audit trail of what was flagged and when.
--
-- Ownership mirrors draft_versions: access is inherited from the parent
-- draft (drafts.user_id_uuid = auth.uid()). Playbook definitions themselves
-- live in migration 20261013000012 (playbooks / playbook_rules).
--
-- Idempotent: safe to re-run.
-- ============================================================

create table if not exists public.draft_checks
(
  id          uuid primary key default gen_random_uuid(),
  draft_id    uuid not null references public.drafts(id) on delete cascade,
  playbook_id uuid references public.playbooks(id) on delete set null,
  -- [{order_index, rule_id, rule_text, clause_type, position, severity, status, note}]
  flags       jsonb not null default '[]'::jsonb,
  -- {total, ok, breach, unclear, breach_high, breach_medium, breach_low}
  summary     jsonb not null default '{}'::jsonb,
  created_by  uuid,
  created_at  timestamptz not null default now()
);

create index if not exists draft_checks_draft_idx
  on public.draft_checks (draft_id, created_at desc);

-- ============================================================
-- Row level security — parent-inherited ownership (like draft_versions)
-- ============================================================
alter table public.draft_checks enable row level security;

drop policy if exists "draft_checks_select_own" on public.draft_checks;
create policy "draft_checks_select_own" on public.draft_checks
  for select to authenticated
  using (exists (
    select 1 from public.drafts d
    where d.id = draft_checks.draft_id and d.user_id_uuid = auth.uid()
  ));

drop policy if exists "draft_checks_insert_own" on public.draft_checks;
create policy "draft_checks_insert_own" on public.draft_checks
  for insert to authenticated
  with check (exists (
    select 1 from public.drafts d
    where d.id = draft_checks.draft_id and d.user_id_uuid = auth.uid()
  ));

drop policy if exists "draft_checks_service_all" on public.draft_checks;
create policy "draft_checks_service_all" on public.draft_checks
  for all to service_role using (true) with check (true);

-- ------------------------------------------------------------
-- Record migration version
-- ------------------------------------------------------------
insert into public.schema_migrations (version, description)
values ('20261013000013', 'draft_checks — persisted playbook check results (RLS owner-only via parent draft)')
on conflict (version) do nothing;

-- ============================================================
-- Migration 0015 — persist draft-section metadata ("Draft Studio")
-- ------------------------------------------------------------
-- draft_sections carried heading / clause_type / content / sources / status,
-- but the per-section `guidance` (the drafting hint) and `required` flag that
-- the skeleton supplies were only attached in memory at create time. Reopening
-- a draft therefore lost both: the guidance hint vanished and every section
-- rendered as required. Persist them on the row so create and reload agree.
--
-- Column defaults match the create-time intent: guidance '' and required true.
-- Existing rows (created before this migration) pick up the defaults.
--
-- Idempotent: safe to re-run.
-- ============================================================

alter table public.draft_sections
  add column if not exists guidance text not null default '';

alter table public.draft_sections
  add column if not exists required boolean not null default true;

-- ------------------------------------------------------------
-- Record migration version
-- ------------------------------------------------------------
insert into public.schema_migrations (version, description)
values ('20261013000015', 'persist draft_sections.guidance + draft_sections.required so skeleton hints survive a reload')
on conflict (version) do nothing;

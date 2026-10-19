-- ============================================================
-- Migration 0012 — legal drafting assistant ("Draft Studio")
-- ------------------------------------------------------------
-- Adds the grounded drafting surface on top of the existing RAG core:
--
--   * draft_templates / draft_clauses — the CURATED template & clause
--     library the firm maintains (firm positions, standard language).
--     Readable by authenticated users at/under their access ceiling;
--     written only by the service role (curation, not user content).
--   * drafts / draft_sections / draft_versions — user-authored drafts.
--     Ownership RLS mirrors chat_sessions exactly (owner = auth.uid()),
--     so a user can only ever read/write their own drafts. Draft content
--     is grounded in retrieved precedents + the clause library, and each
--     section carries its own sources jsonb + a status flag; a section
--     whose retrieval returned nothing is stored as 'needs_source' and is
--     NEVER fabricated (see agents/draft_agent.py).
--   * playbooks / playbook_rules — firm review positions ("required",
--     "prohibited", "fallback", ...) used to check a draft after it is
--     written. Same read/write split as the clause library.
--
-- Embedding dim is 768, matching settings.embedding_dim and the retyped
-- documents.embedding column (migration 0007). Changing the embedder means
-- re-embedding draft_clauses too.
--
-- Idempotent: safe to re-run.
-- ============================================================

-- ------------------------------------------------------------
-- draft_templates — curated skeletons (one row per doc type)
-- ------------------------------------------------------------
create table if not exists public.draft_templates
(
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid references public.tenants(id),
  doc_type      text not null,
  practice_area text not null default '',
  jurisdiction  text not null default 'ke',
  title         text not null,
  description   text not null default '',
  -- [{heading, clause_type, guidance, required}]
  skeleton      jsonb not null default '[]'::jsonb,
  access_level  integer not null default 1,
  matter_id     text not null default '',
  is_active     boolean not null default true,
  created_by    uuid,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create index if not exists draft_templates_doc_type_idx
  on public.draft_templates (doc_type);

-- ------------------------------------------------------------
-- draft_clauses — curated clause bank (hybrid-searchable)
-- ------------------------------------------------------------
create table if not exists public.draft_clauses
(
  id             uuid primary key default gen_random_uuid(),
  tenant_id      uuid references public.tenants(id),
  doc_type       text not null default '',
  practice_area  text not null default '',
  clause_type    text not null default '',
  title          text not null,
  body           text not null,
  is_standard    boolean not null default true,
  access_level   integer not null default 1,
  matter_id      text not null default '',
  source_file_id text,
  embedding      vector(768),
  -- Generated so keyword search can never drift from the row content.
  search_tsv     tsvector generated always as
                   (to_tsvector('english', coalesce(title, '') || ' ' || coalesce(body, ''))) stored,
  created_by     uuid,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);

create index if not exists draft_clauses_clause_type_idx
  on public.draft_clauses (clause_type);
create index if not exists draft_clauses_fts_idx
  on public.draft_clauses using gin (search_tsv);

-- ------------------------------------------------------------
-- drafts — one row per draft document, owned by its author
-- ------------------------------------------------------------
create table if not exists public.drafts
(
  id              uuid primary key default gen_random_uuid(),
  tenant_id       uuid references public.tenants(id),
  user_id_uuid    uuid not null,
  matter_id       text not null default '',
  title           text not null,
  doc_type        text not null,
  template_id     uuid references public.draft_templates(id),
  brief           jsonb not null default '{}'::jsonb,
  status          text not null default 'draft'
                    check (status in ('draft', 'review', 'final', 'archived')),
  access_level    integer not null default 1,
  current_version integer not null default 1,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

create index if not exists drafts_user_idx on public.drafts (user_id_uuid);
create index if not exists drafts_updated_idx on public.drafts (updated_at desc);

-- ------------------------------------------------------------
-- draft_sections — ordered sections, each grounded independently
-- ------------------------------------------------------------
create table if not exists public.draft_sections
(
  id          uuid primary key default gen_random_uuid(),
  draft_id    uuid not null references public.drafts(id) on delete cascade,
  order_index integer not null default 0,
  heading     text not null default '',
  clause_type text not null default '',
  content     text not null default '',
  sources     jsonb not null default '[]'::jsonb,
  status      text not null default 'empty'
                check (status in ('empty', 'generated', 'edited', 'needs_source')),
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  unique (draft_id, order_index)
);

create index if not exists draft_sections_draft_idx
  on public.draft_sections (draft_id, order_index);

-- ------------------------------------------------------------
-- draft_versions — immutable snapshots for version history
-- ------------------------------------------------------------
create table if not exists public.draft_versions
(
  id         uuid primary key default gen_random_uuid(),
  draft_id   uuid not null references public.drafts(id) on delete cascade,
  version    integer not null,
  snapshot   jsonb not null default '{}'::jsonb,
  label      text not null default '',
  created_by uuid,
  created_at timestamptz not null default now(),
  unique (draft_id, version)
);

create index if not exists draft_versions_draft_idx
  on public.draft_versions (draft_id, version desc);

-- ------------------------------------------------------------
-- playbooks / playbook_rules — firm review positions
-- ------------------------------------------------------------
create table if not exists public.playbooks
(
  id           uuid primary key default gen_random_uuid(),
  tenant_id    uuid references public.tenants(id),
  name         text not null,
  doc_type     text not null default '',
  description  text not null default '',
  access_level integer not null default 1,
  matter_id    text not null default '',
  is_active    boolean not null default true,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);

create table if not exists public.playbook_rules
(
  id          uuid primary key default gen_random_uuid(),
  playbook_id uuid not null references public.playbooks(id) on delete cascade,
  order_index integer not null default 0,
  clause_type text not null default '',
  position    text not null default 'advisory'
                check (position in ('required', 'preferred', 'fallback', 'prohibited', 'advisory')),
  rule_text   text not null,
  severity    text not null default 'medium'
                check (severity in ('low', 'medium', 'high')),
  created_at  timestamptz not null default now()
);

create index if not exists playbook_rules_playbook_idx
  on public.playbook_rules (playbook_id, order_index);

-- ============================================================
-- Row level security
-- ============================================================
alter table public.drafts          enable row level security;
alter table public.draft_sections  enable row level security;
alter table public.draft_versions  enable row level security;
alter table public.draft_templates enable row level security;
alter table public.draft_clauses   enable row level security;
alter table public.playbooks       enable row level security;
alter table public.playbook_rules  enable row level security;

-- drafts: owner-only, exactly like chat_sessions.
drop policy if exists "drafts_select_own" on public.drafts;
create policy "drafts_select_own" on public.drafts
  for select to authenticated
  using (user_id_uuid = auth.uid());

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

drop policy if exists "drafts_service_all" on public.drafts;
create policy "drafts_service_all" on public.drafts
  for all to service_role using (true) with check (true);

-- draft_sections: ownership inherited from the parent draft. The subquery
-- is safe from RLS recursion because drafts' own policies never reference
-- draft_sections.
drop policy if exists "draft_sections_select_own" on public.draft_sections;
create policy "draft_sections_select_own" on public.draft_sections
  for select to authenticated
  using (exists (
    select 1 from public.drafts d
    where d.id = draft_sections.draft_id and d.user_id_uuid = auth.uid()
  ));

drop policy if exists "draft_sections_insert_own" on public.draft_sections;
create policy "draft_sections_insert_own" on public.draft_sections
  for insert to authenticated
  with check (exists (
    select 1 from public.drafts d
    where d.id = draft_sections.draft_id and d.user_id_uuid = auth.uid()
  ));

drop policy if exists "draft_sections_update_own" on public.draft_sections;
create policy "draft_sections_update_own" on public.draft_sections
  for update to authenticated
  using (exists (
    select 1 from public.drafts d
    where d.id = draft_sections.draft_id and d.user_id_uuid = auth.uid()
  ))
  with check (exists (
    select 1 from public.drafts d
    where d.id = draft_sections.draft_id and d.user_id_uuid = auth.uid()
  ));

drop policy if exists "draft_sections_delete_own" on public.draft_sections;
create policy "draft_sections_delete_own" on public.draft_sections
  for delete to authenticated
  using (exists (
    select 1 from public.drafts d
    where d.id = draft_sections.draft_id and d.user_id_uuid = auth.uid()
  ));

drop policy if exists "draft_sections_service_all" on public.draft_sections;
create policy "draft_sections_service_all" on public.draft_sections
  for all to service_role using (true) with check (true);

-- draft_versions: same parent-inherited ownership.
drop policy if exists "draft_versions_select_own" on public.draft_versions;
create policy "draft_versions_select_own" on public.draft_versions
  for select to authenticated
  using (exists (
    select 1 from public.drafts d
    where d.id = draft_versions.draft_id and d.user_id_uuid = auth.uid()
  ));

drop policy if exists "draft_versions_insert_own" on public.draft_versions;
create policy "draft_versions_insert_own" on public.draft_versions
  for insert to authenticated
  with check (exists (
    select 1 from public.drafts d
    where d.id = draft_versions.draft_id and d.user_id_uuid = auth.uid()
  ));

drop policy if exists "draft_versions_service_all" on public.draft_versions;
create policy "draft_versions_service_all" on public.draft_versions
  for all to service_role using (true) with check (true);

-- Curated library (templates, clauses, playbooks, rules): readable by any
-- authenticated user at/under their ceiling and within their matters;
-- written only by the service role.
drop policy if exists "draft_templates_select_authorized" on public.draft_templates;
create policy "draft_templates_select_authorized" on public.draft_templates
  for select to authenticated
  using (
    access_level <= public.auth_user_access_level()
    and (matter_id = '' or public.auth_can_access_matter_ref(matter_id))
  );

drop policy if exists "draft_templates_service_all" on public.draft_templates;
create policy "draft_templates_service_all" on public.draft_templates
  for all to service_role using (true) with check (true);

drop policy if exists "draft_clauses_select_authorized" on public.draft_clauses;
create policy "draft_clauses_select_authorized" on public.draft_clauses
  for select to authenticated
  using (
    access_level <= public.auth_user_access_level()
    and (matter_id = '' or public.auth_can_access_matter_ref(matter_id))
  );

drop policy if exists "draft_clauses_service_all" on public.draft_clauses;
create policy "draft_clauses_service_all" on public.draft_clauses
  for all to service_role using (true) with check (true);

drop policy if exists "playbooks_select_authorized" on public.playbooks;
create policy "playbooks_select_authorized" on public.playbooks
  for select to authenticated
  using (
    access_level <= public.auth_user_access_level()
    and (matter_id = '' or public.auth_can_access_matter_ref(matter_id))
  );

drop policy if exists "playbooks_service_all" on public.playbooks;
create policy "playbooks_service_all" on public.playbooks
  for all to service_role using (true) with check (true);

drop policy if exists "playbook_rules_select_authorized" on public.playbook_rules;
create policy "playbook_rules_select_authorized" on public.playbook_rules
  for select to authenticated
  using (exists (
    select 1 from public.playbooks p
    where p.id = playbook_rules.playbook_id
      and p.access_level <= public.auth_user_access_level()
      and (p.matter_id = '' or public.auth_can_access_matter_ref(p.matter_id))
  ));

drop policy if exists "playbook_rules_service_all" on public.playbook_rules;
create policy "playbook_rules_service_all" on public.playbook_rules
  for all to service_role using (true) with check (true);

-- ============================================================
-- Clause retrieval RPCs — SECURITY INVOKER, auth predicates, so the
-- function predicate and the table RLS policy cannot disagree.
-- ============================================================
create or replace function public.match_draft_clauses(
  query_embedding    vector,
  match_count        int  default 10,
  doc_type_filter    text default null,
  clause_type_filter text default null
)
returns table (id uuid, title text, body text, clause_type text, doc_type text,
               access_level int, matter_id text, similarity float)
language sql
security invoker
set search_path = public, extensions
as $$
  select
    c.id,
    c.title,
    c.body,
    c.clause_type,
    c.doc_type,
    c.access_level,
    c.matter_id,
    1 - (c.embedding <=> query_embedding) as similarity
  from public.draft_clauses c
  where
    c.embedding is not null
    and c.access_level <= public.auth_user_access_level()
    and (c.matter_id = '' or public.auth_can_access_matter_ref(c.matter_id))
    and (doc_type_filter is null or c.doc_type = doc_type_filter or c.doc_type = '')
    and (clause_type_filter is null or c.clause_type = clause_type_filter)
  order by c.embedding <=> query_embedding
  limit match_count;
$$;

-- Recreate (drop first) because the return type gains a `similarity` column;
-- ``create or replace`` cannot change a function's OUT signature. Grants are
-- re-issued at the bottom of the file.
drop function if exists public.hybrid_search_clauses(text, vector, int, int, text, text);

create or replace function public.hybrid_search_clauses(
  query_text         text,
  query_embedding    vector,
  match_count        int  default 10,
  rrf_k              int  default 60,
  doc_type_filter    text default null,
  clause_type_filter text default null
)
returns table (id uuid, title text, body text, clause_type text, doc_type text,
               access_level int, matter_id text, rrf_score float, similarity float)
language plpgsql
security invoker
set search_path = public, extensions
as $$
begin
  return query
  with vector_results as (
    select
      c.id,
      row_number() over (order by c.embedding <=> query_embedding) as rank
    from public.draft_clauses c
    where c.embedding is not null
      and c.access_level <= public.auth_user_access_level()
      and (c.matter_id = '' or public.auth_can_access_matter_ref(c.matter_id))
      and (doc_type_filter is null or c.doc_type = doc_type_filter or c.doc_type = '')
      and (clause_type_filter is null or c.clause_type = clause_type_filter)
    order by c.embedding <=> query_embedding
    limit match_count
  ),
  keyword_results as (
    select
      c.id,
      row_number() over (
        order by ts_rank_cd(c.search_tsv, plainto_tsquery('english', query_text)) desc
      ) as rank
    from public.draft_clauses c
    where c.search_tsv @@ plainto_tsquery('english', query_text)
      and c.access_level <= public.auth_user_access_level()
      and (c.matter_id = '' or public.auth_can_access_matter_ref(c.matter_id))
      and (doc_type_filter is null or c.doc_type = doc_type_filter or c.doc_type = '')
      and (clause_type_filter is null or c.clause_type = clause_type_filter)
    order by ts_rank_cd(c.search_tsv, plainto_tsquery('english', query_text)) desc
    limit match_count
  ),
  fused as (
    select
      ranked.id,
      sum(ranked.score)::float as rrf_score
    from (
      select vector_results.id, 1.0 / (rrf_k + vector_results.rank) as score from vector_results
      union all
      select keyword_results.id, 1.0 / (rrf_k + keyword_results.rank) as score from keyword_results
    ) ranked
    group by ranked.id
  )
  select
    c.id,
    c.title,
    c.body,
    c.clause_type,
    c.doc_type,
    c.access_level,
    c.matter_id,
    fused.rrf_score,
    -- Vector cosine similarity, so callers can apply a relevance floor: the
    -- no-source guard must treat an unrelated clause as NO grounding, not
    -- silently ground a section on the nearest-but-irrelevant clause.
    1 - (c.embedding <=> query_embedding) as similarity
  from fused
  join public.draft_clauses c on c.id = fused.id
  order by fused.rrf_score desc
  limit match_count;
end;
$$;

grant execute on function public.match_draft_clauses(vector, int, text, text)
  to authenticated;
grant execute on function public.hybrid_search_clauses(text, vector, int, int, text, text)
  to authenticated;

-- ------------------------------------------------------------
-- Record migration version
-- ------------------------------------------------------------
insert into public.schema_migrations (version, description)
values ('20261013000012', 'legal drafting assistant (draft_templates, draft_clauses, drafts, draft_sections, draft_versions, playbooks, playbook_rules + RLS + clause retrieval RPCs)')
on conflict (version) do nothing;

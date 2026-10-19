-- ============================================================
-- Migration 0016 — per-account document privacy
-- ------------------------------------------------------------
-- GOAL (product requirement):
--   A user's own uploads are visible to that user alone (plus anyone they
--   explicitly share the file with). Nobody else — not a colleague in the same
--   matter, not a level-5 partner, not an admin — can see, list, open or
--   retrieve another account's upload. Access levels and matter grants keep
--   working exactly as before for the firm corpus (Drive-ingested documents).
--
-- WHY THIS NEEDS MORE THAN A POLICY TWEAK
--   Before this migration there was NO ownership anywhere in the data model:
--   visibility was only `access_level <= auth_user_access_level()` AND
--   (`matter_id = ''` OR `auth_can_access_matter_ref(matter_id)`). The
--   uploader's identity survived only as a string prefix inside file_id
--   (`upload_<uuid>_<filename>`), which no policy read. So the column has to
--   exist before a policy can enforce it, and the upload path has to stamp it
--   (writes go through the service role, which bypasses RLS by design).
--
-- MODEL
--   doc_scope = 'corpus'   → firm corpus. Visible per the pre-existing
--                            access_level + matter rules. owner_user_id is
--                            normally NULL (a Drive file has no uploader).
--   doc_scope = 'private'  → one account's upload. Requires owner_user_id.
--                            Visible to the owner and to users in
--                            public.document_shares for that file_id, AND
--                            still subject to the level/matter ceilings.
--
--   The scope is an explicit column rather than "owner_user_id IS NULL means
--   shared" on purpose: if the application ever fails to stamp an owner on an
--   upload, the row keeps its default ('corpus', no owner) and is governed by
--   the corpus rules — but the CHECK constraint below makes a *private* row
--   without an owner impossible, so the private branch can never fail open.
--
-- Idempotent: safe to re-run.
-- ============================================================

-- ------------------------------------------------------------
-- 1. Ownership / scope columns
-- ------------------------------------------------------------
alter table public.documents
  add column if not exists owner_user_id uuid,
  add column if not exists doc_scope text not null default 'corpus';

alter table public.document_metadata
  add column if not exists owner_user_id uuid,
  add column if not exists doc_scope text not null default 'corpus';

do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conname = 'documents_doc_scope_check' and conrelid = 'public.documents'::regclass
  ) then
    alter table public.documents
      add constraint documents_doc_scope_check
      check (doc_scope in ('corpus', 'private'));
  end if;

  if not exists (
    select 1 from pg_constraint
    where conname = 'documents_private_owner_check' and conrelid = 'public.documents'::regclass
  ) then
    alter table public.documents
      add constraint documents_private_owner_check
      check (doc_scope <> 'private' or owner_user_id is not null);
  end if;

  if not exists (
    select 1 from pg_constraint
    where conname = 'document_metadata_doc_scope_check' and conrelid = 'public.document_metadata'::regclass
  ) then
    alter table public.document_metadata
      add constraint document_metadata_doc_scope_check
      check (doc_scope in ('corpus', 'private'));
  end if;

  if not exists (
    select 1 from pg_constraint
    where conname = 'document_metadata_private_owner_check' and conrelid = 'public.document_metadata'::regclass
  ) then
    alter table public.document_metadata
      add constraint document_metadata_private_owner_check
      check (doc_scope <> 'private' or owner_user_id is not null);
  end if;
end
$$;

create index if not exists documents_owner_idx
  on public.documents (owner_user_id) where owner_user_id is not null;
create index if not exists document_metadata_owner_idx
  on public.document_metadata (owner_user_id) where owner_user_id is not null;

-- ------------------------------------------------------------
-- 2. Explicit shares (owner grants named accounts read access)
-- ------------------------------------------------------------
create table if not exists public.document_shares
(
  id          uuid primary key default gen_random_uuid(),
  file_id     text not null,
  -- Not an FK to auth.users: the rest of this schema deliberately keeps auth
  -- references as bare uuids (auth is a Supabase-managed schema, absent from a
  -- plain Postgres test database). A stale row for a deleted account is inert —
  -- no auth.uid() can ever match it — so this fails closed.
  shared_with uuid not null,
  created_by  uuid not null,
  created_at  timestamptz not null default now(),
  unique (file_id, shared_with)
);

create index if not exists document_shares_file_idx on public.document_shares (file_id);
create index if not exists document_shares_shared_with_idx on public.document_shares (shared_with);

-- ------------------------------------------------------------
-- 3. Authorization helpers (SECURITY DEFINER — they must read the
--    FORCE-RLS documents table to decide, exactly like the pre-existing
--    security-definer delete_documents_by_file_id_before pattern).
-- ------------------------------------------------------------

-- True when the caller owns the file. Used as the WITH CHECK on
-- document_shares inserts: without it, any authenticated user could insert a
-- share row for somebody else's file_id naming themselves, and the documents
-- SELECT policy below would then hand them the file.
create or replace function public.auth_owns_document(p_file_id text)
returns boolean
language sql stable security definer
set search_path = public, pg_temp
as $$
  select exists (
    select 1
    from public.documents d
    where d.metadata->>'file_id' = p_file_id
      and d.doc_scope = 'private'
      and d.owner_user_id = auth.uid()
  );
$$;

-- True when the file has been explicitly shared with the caller.
create or replace function public.auth_document_shared_with_me(p_file_id text)
returns boolean
language sql stable security definer
set search_path = public, pg_temp
as $$
  select exists (
    select 1
    from public.document_shares s
    where s.file_id = p_file_id
      and s.shared_with = auth.uid()
  );
$$;

grant execute on function public.auth_owns_document(text) to authenticated;
grant execute on function public.auth_document_shared_with_me(text) to authenticated;

-- ------------------------------------------------------------
-- 3b. Share management (owner-driven)
-- ------------------------------------------------------------
-- SECURITY DEFINER because they must read auth.users (to resolve an email),
-- the *recipient's* user_profiles row and their matter_access grants — none of
-- which the caller can see under RLS. Every function re-derives the caller from
-- auth.uid() and refuses unless the caller owns the file, so the elevated read
-- never widens what the caller can learn: it only answers "may this named
-- colleague read this file?".
create or replace function public.share_document_by_email(p_file_id text, p_email text)
returns uuid
language plpgsql volatile security definer
set search_path = public, pg_temp
as $$
declare
  v_owner         uuid;
  v_level         integer;
  v_matter        text;
  v_target        uuid;
  v_target_level  integer;
  v_target_tenant uuid;
begin
  select d.owner_user_id, d.access_level, d.matter_id
    into v_owner, v_level, v_matter
  from public.documents d
  where d.metadata->>'file_id' = p_file_id
    and d.doc_scope = 'private'
  limit 1;

  if v_owner is null then
    raise exception 'not_found: no private document with file_id %', p_file_id
      using errcode = 'P0002';
  end if;
  if v_owner <> auth.uid() then
    raise exception 'forbidden: only the owner can share this document'
      using errcode = 'P0001';
  end if;

  select u.id into v_target
  from auth.users u
  where lower(u.email) = lower(trim(p_email))
  limit 1;

  if v_target is null then
    raise exception 'not_found: no user with email %', p_email using errcode = 'P0002';
  end if;
  if v_target = v_owner then
    raise exception 'invalid: you cannot share a document with yourself'
      using errcode = 'P0001';
  end if;

  select p.access_level, p.tenant_id into v_target_level, v_target_tenant
  from public.user_profiles p
  where p.user_id = v_target;

  if v_target_level is null then
    raise exception 'not_found: recipient has no firm profile' using errcode = 'P0002';
  end if;
  if v_target_tenant is distinct from public.auth_tenant_id() then
    raise exception 'forbidden: recipient is not in your firm' using errcode = 'P0001';
  end if;
  -- The share cannot exceed the document's own classification: an explicit
  -- share must never hand a level-3 file to a level-1 account, and a matter
  -- document stays inside its matter.
  if v_target_level < v_level then
    raise exception 'forbidden: recipient access level % is below this document''s level %',
      v_target_level, v_level using errcode = 'P0001';
  end if;
  if v_matter <> ''
     and not exists (
       select 1
       from public.matters m
       join public.matter_access ma on ma.matter_id = m.id
       where m.matter_ref = v_matter
         and ma.user_id = v_target
     )
     and not exists (
       select 1 from public.user_profiles p2
       where p2.user_id = v_target and p2.firm_wide
     ) then
    raise exception 'forbidden: recipient has no grant for matter %', v_matter
      using errcode = 'P0001';
  end if;

  insert into public.document_shares (file_id, shared_with, created_by)
  values (p_file_id, v_target, v_owner)
  on conflict (file_id, shared_with) do nothing;

  return v_target;
end;
$$;

create or replace function public.unshare_document(p_file_id text, p_shared_with uuid)
returns void
language plpgsql volatile security definer
set search_path = public, pg_temp
as $$
begin
  if not public.auth_owns_document(p_file_id) then
    raise exception 'forbidden: only the owner can change sharing on this document'
      using errcode = 'P0001';
  end if;
  delete from public.document_shares
  where file_id = p_file_id
    and shared_with = p_shared_with
    and created_by = auth.uid();
end;
$$;

create or replace function public.list_document_shares(p_file_id text)
returns table (shared_with uuid, email text, created_at timestamptz)
language sql stable security definer
set search_path = public, pg_temp
as $$
  select s.shared_with, u.email::text, s.created_at
  from public.document_shares s
  left join auth.users u on u.id = s.shared_with
  where s.file_id = p_file_id
    and s.created_by = auth.uid()
  order by s.created_at;
$$;

grant execute on function public.share_document_by_email(text, text) to authenticated;
grant execute on function public.unshare_document(text, uuid) to authenticated;
grant execute on function public.list_document_shares(text) to authenticated;

-- ------------------------------------------------------------
-- 4. RLS on document_shares
-- ------------------------------------------------------------
alter table public.document_shares enable row level security;
alter table public.document_shares force row level security;

-- Only SELECT is granted to `authenticated`: every mutation runs through the
-- SECURITY DEFINER functions above (`share_document_by_email`,
-- `unshare_document`), which run as the table owner. The INSERT/DELETE policies
-- below are therefore defence-in-depth for a future direct write, not the
-- current path.
grant select on public.document_shares to authenticated;
grant select, insert, update, delete on public.document_shares to service_role;

-- A sharee sees the rows naming them (so the UI can mark a document as shared
-- with them); the owner sees the rows they created.
drop policy if exists "document_shares_select_own" on public.document_shares;
create policy "document_shares_select_own" on public.document_shares
  for select to authenticated
  using (shared_with = auth.uid() or created_by = auth.uid());

drop policy if exists "document_shares_insert_owner" on public.document_shares;
create policy "document_shares_insert_owner" on public.document_shares
  for insert to authenticated
  with check (
    created_by = auth.uid()
    and public.auth_owns_document(file_id)
  );

drop policy if exists "document_shares_delete_owner" on public.document_shares;
create policy "document_shares_delete_owner" on public.document_shares
  for delete to authenticated
  using (created_by = auth.uid());

-- ------------------------------------------------------------
-- 5. Documents / document_metadata SELECT policies
-- ------------------------------------------------------------
-- Corpus rows keep the exact previous behaviour. Private rows add an
-- owner-or-sharee test. The level/matter ceilings still apply to BOTH, so a
-- private upload is never reachable outside the bounds it was classified
-- into (the uploader always satisfies them for their own file, because
-- authz.policy.classify_upload derives them from the uploader's own profile
-- and grants).
--
-- Note: this policy applies to BOTH tables; the share/ownership tests use the
-- chunk's metadata->>'file_id' on documents and the file_id column on
-- document_metadata.
drop policy if exists "auth_uid can select documents" on public.documents;
create policy "auth_uid can select documents" on public.documents
  for select to authenticated
  using (
    access_level <= public.auth_user_access_level()
    and (
      matter_id = ''
      or public.auth_can_access_matter_ref(matter_id)
    )
    and (
      doc_scope = 'corpus'
      or owner_user_id = auth.uid()
      or public.auth_document_shared_with_me(metadata->>'file_id')
    )
  );

drop policy if exists "auth_uid can select document_metadata" on public.document_metadata;
create policy "auth_uid can select document_metadata" on public.document_metadata
  for select to authenticated
  using (
    access_level <= public.auth_user_access_level()
    and (
      matter_id = ''
      or public.auth_can_access_matter_ref(matter_id)
    )
    and (
      doc_scope = 'corpus'
      or owner_user_id = auth.uid()
      or public.auth_document_shared_with_me(file_id)
    )
  );

-- ------------------------------------------------------------
-- 6. Backfill existing rows
-- ------------------------------------------------------------
-- UI uploads are identifiable by the `upload_<uuid>_<name>` file_id the
-- upload endpoint has always generated, so ownership is recoverable exactly.
-- Everything else (Drive ids, webhook/worker ingest) is firm corpus.
update public.documents
set owner_user_id = split_part(metadata->>'file_id', '_', 2)::uuid,
    doc_scope = 'private'
where doc_scope = 'corpus'
  and metadata->>'file_id' ~ '^upload_[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}_';

update public.document_metadata
set owner_user_id = split_part(file_id, '_', 2)::uuid,
    doc_scope = 'private'
where doc_scope = 'corpus'
  and file_id ~ '^upload_[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}_';

-- ------------------------------------------------------------
-- 7. Retrieval RPCs — same predicate as the policy, so the function
--    predicate and RLS cannot disagree (both are SECURITY INVOKER and
--    additionally evaluated against the caller).
-- ------------------------------------------------------------
drop function if exists public.match_documents_rls(vector, int, jsonb);

create or replace function public.match_documents_rls(
  query_embedding vector,
  match_count     int   default 20,
  filter          jsonb default '{}'::jsonb
)
returns table (id bigint, content text, metadata jsonb, access_level int, matter_id text, similarity float)
language sql
security invoker
set search_path = public, extensions
as $$
  select
    d.id,
    d.content,
    d.metadata,
    d.access_level,
    d.matter_id,
    1 - (d.embedding <=> query_embedding) as similarity
  from public.documents d
  where
    d.embedding is not null
    and (filter = '{}'::jsonb or d.metadata @> filter)
    and d.access_level <= public.auth_user_access_level()
    and (d.matter_id = '' or public.auth_can_access_matter_ref(d.matter_id))
    and (
      d.doc_scope = 'corpus'
      or d.owner_user_id = auth.uid()
      or public.auth_document_shared_with_me(d.metadata->>'file_id')
    )
  order by d.embedding <=> query_embedding
  limit match_count;
$$;

grant execute on function public.match_documents_rls(vector, int, jsonb)
  to authenticated, anon;

create or replace function public.hybrid_search_rls(
  query_text      text,
  query_embedding vector,
  match_count     int   default 20,
  rrf_k           int   default 60,
  doc_type_filter text  default null
)
returns table (id bigint, content text, metadata jsonb, access_level int, matter_id text, rrf_score float)
language plpgsql
security invoker
set search_path = public, extensions
as $$
begin
  return query
  with vector_results as (
    select
      d.id,
      row_number() over (order by d.embedding <=> query_embedding) as rank
    from public.documents d
    where d.embedding is not null
      and d.access_level <= public.auth_user_access_level()
      and (d.matter_id = '' or public.auth_can_access_matter_ref(d.matter_id))
      and (
        d.doc_scope = 'corpus'
        or d.owner_user_id = auth.uid()
        or public.auth_document_shared_with_me(d.metadata->>'file_id')
      )
      and (doc_type_filter is null or d.metadata->>'doc_type' = doc_type_filter)
    order by d.embedding <=> query_embedding
    limit match_count
  ),
  keyword_results as (
    select
      d.id,
      row_number() over (
        order by ts_rank_cd(to_tsvector('english', d.content), plainto_tsquery('english', query_text)) desc
      ) as rank
    from public.documents d
    where to_tsvector('english', d.content) @@ plainto_tsquery('english', query_text)
      and d.access_level <= public.auth_user_access_level()
      and (d.matter_id = '' or public.auth_can_access_matter_ref(d.matter_id))
      and (
        d.doc_scope = 'corpus'
        or d.owner_user_id = auth.uid()
        or public.auth_document_shared_with_me(d.metadata->>'file_id')
      )
      and (doc_type_filter is null or d.metadata->>'doc_type' = doc_type_filter)
    order by ts_rank_cd(to_tsvector('english', d.content), plainto_tsquery('english', query_text)) desc
    limit match_count
  ),
  -- `id`/`score` must stay qualified: RETURNS TABLE(id bigint, ...) declares
  -- `id` as an OUT-parameter variable, so an unqualified reference is
  -- ambiguous at call time (see migration 20260727000009).
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
    d.id,
    d.content,
    d.metadata,
    d.access_level,
    d.matter_id,
    fused.rrf_score
  from fused
  join public.documents d on d.id = fused.id
  order by fused.rrf_score desc
  limit match_count;
end;
$$;

grant execute on function public.hybrid_search_rls(text, vector, int, int, text)
  to authenticated, anon;

-- ------------------------------------------------------------
-- Record migration version
-- ------------------------------------------------------------
insert into public.schema_migrations (version, description)
values (
  '20261013000016',
  'per-account document privacy: documents.owner_user_id/doc_scope, document_shares, owner-or-sharee RLS + retrieval predicates, corpus preserved'
)
on conflict (version) do nothing;

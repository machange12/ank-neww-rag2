-- Close four SELECT policies that were created outside these migrations
-- (dashboard) with USING (true) for role public. Permissive policies are
-- OR'ed, so each one overrode the access_level / matter / tenant checks —
-- the public anon key alone could read every document, all metadata, all
-- matters and all user profiles (confirmed 2026-10-13).
--
-- documents / document_metadata / user_profiles already have proper
-- authenticated policies. matters had none, so it gets a tenant-scoped one
-- (matters/lookup.py reads it through the caller's client).

drop policy if exists "documents_select" on public.documents;
drop policy if exists "document_metadata_select" on public.document_metadata;
drop policy if exists "service can read all profiles" on public.user_profiles;

drop policy if exists "users can read matters" on public.matters;
drop policy if exists "matters_select_own_tenant" on public.matters;
create policy "matters_select_own_tenant" on public.matters
  for select to authenticated
  using (tenant_id = public.auth_tenant_id());

-- The ivfflat index was trained on the previous (nomic) vectors; after the
-- switch to Gemini embeddings it searched ~1% of rows in the wrong clusters
-- (hybrid hit@3 59% -> 94% after dropping). Exact scan is ~1ms at this size;
-- add HNSW (migration 0010) when the corpus grows past ~50k chunks.
drop index if exists public.documents_embedding_idx;

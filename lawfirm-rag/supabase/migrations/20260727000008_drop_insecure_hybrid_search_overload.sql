-- ================================================================
-- Migration 0008: drop the insecure legacy 4-arg hybrid_search_rls
--
-- CRITICAL FINDING (live, confirmed, not hypothetical): a live
-- deployment had a pre-existing 4-arg hybrid_search_rls(text, vector,
-- int, int) overload that predates this migration set. It is
-- SECURITY DEFINER and applies NO access_level/matter_id filtering
-- at all (queries `documents` with no WHERE clause on those columns).
--
-- Migration 0004 added a 5-arg hybrid_search_rls(..., doc_type_filter)
-- with proper SECURITY INVOKER + auth_can_access_matter_ref()
-- filtering, but since Postgres resolves overloads by argument count,
-- it did not replace the old 4-arg one — both coexisted. The app
-- (search/hybrid.py) only sends `doc_type_filter` when a query
-- matches a doc-type keyword hint (judgment/contract/statute) — most
-- queries don't, so most queries were resolving to the INSECURE 4-arg
-- overload, bypassing access-level and matter authorization entirely.
--
-- Fix: drop the 4-arg overload. Postgres then resolves any 4-param
-- call to the 5-arg overload using its default (doc_type_filter =
-- null), so no application code change is needed — every call now
-- goes through the properly-scoped SECURITY INVOKER version.
--
-- Idempotent: safe to re-run.
-- ================================================================

drop function if exists public.hybrid_search_rls(text, vector, int, int);

insert into public.schema_migrations (version, description)
values ('20260727000008', 'SECURITY: drop insecure unscoped legacy 4-arg hybrid_search_rls overload (SECURITY DEFINER, no access_level/matter_id filtering)')
on conflict (version) do nothing;

# Migrations and Rollbacks (WP2-6 Operations)

`supabase/migrations/` is the **single source of truth** for database schema.
The legacy `sql/*` files are frozen reference only (see `sql/DEPRECATED.md`).
ADR-0004 records the rationale.

## Migration inventory

| File | Purpose | Changes from prior |
|---|---|---|
| `20260727000000_baseline.sql` | Documents/chunks, search RPCs (`match_documents_rls`, `hybrid_search_rls`, `match_documents`), `set_access_context`, ivfflat index | initial |
| `20260727000001_tenant_and_matter_access.sql` | `tenants`, `user_profiles`, `matter_access` | new tables |
| `20260727000002_legal_evidence.sql` | `legal_sources`, `legal_documents`, `legal_document_versions`, `legal_passages` | new tables |
| `20260727000003_chat_sessions_ownership.sql` | `chat_sessions` ownership (`user_id_uuid`), ownership RLS, backfill note | schema + data backfill |
| `20260727000004_jwt_authorization_retrieval.sql` | `auth.uid()`-derived RLS + RPC predicates for retrieval; JWT-claims authorization; `set_access_context` deprecated | policy changes |
| `20260727000005_audit_security_events.sql` | `audit_security_events` append-only table + policies | new table |
| `20260727000006_atomic_reingest.sql` | `delete_documents_by_file_id_before` (cutoff-scoped delete) so a failed re-ingest keeps the old rows | new function |
| `20260727000007_embedding_dimension_768.sql` | Embedding column resized to the configured 768 dimensions | column type |
| `20260727000008_drop_insecure_hybrid_search_overload.sql` | **Security fix**: drops the live 4-arg `hybrid_search_rls` SECURITY DEFINER overload that skipped level/matter filtering | function drop |
| `20260727000009_fix_hybrid_search_ambiguous_id.sql` | Qualifies `id`/`score` in the `fused` CTE (OUT-parameter ambiguity) | function fix |
| `20260727000010_hnsw_index.sql` | HNSW vector index replaces the old ivfflat index | index |
| `20261013000011_close_open_select_policies.sql` | Closes dashboard-created `USING (true)` SELECT policies; tenant-scopes `matters` | policy changes |
| `20261013000012_drafting.sql` | Draft Studio schema: templates, clauses, drafts, sections, versions, playbooks; owner-only RLS | new tables |
| `20261013000013_draft_checks.sql` | `draft_checks` (playbook-check history), parent-inherited owner RLS | new table |
| `20261013000014_draft_sharing.sql` | Matter-scoped **read-only** draft sharing (`drafts.visibility`), parent-inherited SELECT policies; writes stay owner-only | policy changes |
| `20261013000015_draft_section_metadata.sql` | `draft_sections.guidance` + `.required` persist across reloads | additive columns |
| `20261013000016_document_ownership.sql` | Per-account document privacy: `owner_user_id`/`doc_scope`, `document_shares`, owner-or-sharee predicate in the policies and both retrieval RPCs, upload backfill (ADR-0007) | schema + policies + functions |

All files are idempotent (`create table if not exists`,
`create or replace function`, guarded grants) so re-running is safe.

## Applying

1. Back up the database (Supabase Dashboard → Database → Backups, or `pg_dump`).
2. Apply the files in ascending order in the Supabase SQL Editor.
3. Verify: `scripts/verify_schema.py --manifest-only` (offline manifest check)
   and, with a live DB, `scripts/verify_schema.py` (checks tables, functions,
   RLS, policies).

## Rollback

There is no `down` migration: rollback = restore the pre-migration backup and
confirm the manifest for the prior version. Because migrations are append-only
and additive, a partial forward state is safe to complete.

## Verification commands

```bash
cd lawfirm-rag
../.venv\Scripts\python scripts/verify_schema.py --manifest-only   # offline (Windows)
../.venv\Scripts\python scripts/verify_schema.py                   # live DB (Windows)
../.venv\Scripts\python -m pytest tests/ -q                        # offline suite (Windows)
# macOS / Linux: replace ../.venv\Scripts\python with ../.venv/bin/python,
# or use the repo-root Makefile targets `make verify` / `make test`.
```

## Guardrails

- Never edit a migration that has been applied to a shared environment; add a
  new migration instead.
- Never create schema outside `supabase/migrations/`.
- New RPCs used by the backend must be granted to `authenticated` (and, for
  internal write helpers, `service_role` only) — never to `anon` unless
  explicitly required.
- Keep the service-role key out of user-facing read paths (see
  `docs/security/threat-model.md`, T6).
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
.venv\Scripts\python scripts/verify_schema.py --manifest-only   # offline
.venv\Scripts\python scripts/verify_schema.py                   # live DB
.venv\Scripts\python -m pytest tests/ -q                        # offline suite
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
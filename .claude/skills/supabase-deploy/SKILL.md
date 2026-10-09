---
name: supabase-deploy
description: Add or apply a database migration for the law-firm RAG (Supabase Postgres) safely — new migration file, backup, apply in order, verify schema, check RLS. Use when the user changes the schema, adds an RPC or policy, says migrate / deploy the database, or touches lawfirm-rag/supabase/migrations.
---

# Database migrations (lawfirm-rag)

`lawfirm-rag/supabase/migrations/` is the **only** source of truth for schema. `schema.sql` and `sql/*` are
deprecated reference — never apply or edit them. Full guide: `docs/operations/migrations-and-rollbacks.md`.

## Writing a migration

1. New file: `YYYYMMDDHHMMSS_short_name.sql`, numbered after the latest one in the folder. **Never edit a
   migration that has been applied** — add a new one.
2. Make it idempotent: `create table if not exists`, `create or replace function`, guarded grants, `drop … if
   exists` for overloads you replace.
3. Security rules:
   - RLS on every user-facing table; predicates derive from `auth.uid()` (not `set_access_context`, deprecated).
   - New RPCs used by the backend: grant to `authenticated`; internal write helpers to `service_role` only;
     **never** `anon` unless explicitly required.
   - Avoid `SECURITY DEFINER` on retrieval functions. If you replace a function's signature, drop the old overload
     (migration `0008` exists because a stale overload bypassed authorization).
4. If you add a table, function or policy the app depends on, add it to the manifest checked by
   `scripts/verify_schema.py` / `tests/test_schema_manifest.py`.
5. Embedding dimension is pinned by `EMBEDDING_DIM` (768). Changing it needs a column re-type migration and a full
   re-ingest — flag this to the user.

## Applying

1. **Back up** (Supabase Dashboard → Database → Backups, or `pg_dump`). There is no down migration; rollback =
   restore the backup.
2. Offline check first: `python scripts/verify_schema.py --manifest-only` and `python -m pytest tests/ -q`.
3. Apply, from `lawfirm-rag/`, using one of the supported paths:
   - `python scripts/apply_migrations.py --dry-run`, then without `--dry-run` (records versions in
     `public.schema_migrations`; uses `POSTGRES_DSN`)
   - or `supabase link --project-ref <ref>` then `supabase db push`
4. Verify against the live DB: `python scripts/verify_schema.py`.
5. Run Supabase's security advisors (Supabase MCP `get_advisors`) and fix new RLS/grant warnings.
6. Update the migrations table in `README.md` and `docs/operations/migrations-and-rollbacks.md`.

Confirm with the user before applying to any database that holds real firm data.

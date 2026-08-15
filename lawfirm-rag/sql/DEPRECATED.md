# DEPRECATED — legacy SQL sources of truth

Do NOT apply these files to a new or existing database.

The authoritative, versioned schema now lives in
[`supabase/migrations/`](../supabase/migrations/). There is exactly one
documented migration path — see the **Migrations** section of the
README and [`docs/operations/migrations-and-rollbacks.md`](../docs/operations/migrations-and-rollbacks.md).

| Legacy file | Replaced by | Notes |
|---|---|---|
| root `schema.sql` | `20260727000000_baseline.sql` | Used `app.*` GUCs + a single `matter_id`. Superseded by the `lawfirm.*`/JWT-claims design. |
| `sql/schema.sql` | `20260727000000_baseline.sql` | Used `lawfirm.*` GUCs + `bigserial` ids. |
| `sql/set_access_context.sql` | `20260727000000_baseline.sql`, then deprecated by `20260727000004` | GUC-based access does not survive across PostgREST requests. |
| `sql/functions.sql` | `20260727000000_baseline.sql`, then `20260727000004_jwt_authorization_retrieval.sql` | |
| `sql/match_documents_rls.sql` | `20260727000000_baseline.sql`, then `20260727000004_jwt_authorization_retrieval.sql` | |
| `sql/rls_policies.sql` | `20260727000000_baseline.sql`, then `20260727000004_jwt_authorization_retrieval.sql` | |

Applying any of these files after the migrations have run can silently
re-introduce the GUC-based (non-surviving) authorization model and
conflict with the JWT-claims policies created in migration
`20260727000004`. If you are troubleshooting, run
`python scripts/verify_schema.py` first.

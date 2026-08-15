# ADR-0004 — One Migration System: `supabase/migrations/`

**Status:** Accepted (2026)

**Context.** Earlier work produced ad-hoc `sql/*.sql` files applied manually in a
fixed order, plus further `ALTER TABLE` statements scattered through `CHANGES.md`
and the README. That makes schema state implicit and unverifiable.

**Decision.**

1. `supabase/migrations/` (timestamped, `20260727000000`…`0005`) is the **single
   source of truth**.
2. `sql/*` is frozen reference only and marked deprecated
   (`sql/DEPRECATED.md`).
3. Migrations are **additive and idempotent** (`create if not exists`,
   `create or replace function`); there is no `down` migration — rollback is via
   backup restore (see `docs/operations/migrations-and-rollbacks.md`).
4. `scripts/verify_schema.py` (with `--manifest-only` offline mode) checks the
   required migration set and key DDL.

**Consequences.** One clear apply order, an offline manifest test
(`test_schema_manifest.py`), and a defined rollback contract. Never edit an
applied migration; append a new one.
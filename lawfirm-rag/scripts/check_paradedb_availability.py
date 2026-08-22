"""
check_paradedb_availability.py
================================
Phase 0 pre-flight checkpoint for the BM25 migration (Phase 4): prints the
SQL to check whether ParadeDB's pg_search/BM25 extension is installed or at
least available on this Supabase project, BEFORE any BM25 migration work is
scheduled. Standard hosted Supabase tiers commonly do not expose ParadeDB by
default (it typically needs Supabase's dedicated integration or a
self-hosted Postgres with the extension compiled in) — this must be
confirmed against the actual project, not assumed.

There is no generic raw-SQL RPC in this codebase (by design — every RPC is a
narrow, named, RLS-aware function; see supabase/migrations/), so this check
cannot run over the Supabase Python client. Run the printed SQL directly via
the Supabase SQL editor or `psql $POSTGRES_DSN` (config.py's postgres_dsn),
and check Supabase dashboard -> Database -> Extensions for 'pg_search' /
ParadeDB as a second confirmation.

Usage:
    python scripts/check_paradedb_availability.py
"""

CHECK_SQL = """\
select name, installed_version, default_version
from pg_available_extensions
where name ilike '%search%' or name ilike '%bm25%'
order by name;
"""


def main() -> None:
    print(__doc__)
    print("Run this against the project's Postgres instance:\n")
    print(CHECK_SQL)
    print(
        "Decision:\n"
        "  - If 'pg_search' (or another ParadeDB BM25 extension) appears with a\n"
        "    non-null default_version -> Phase 4 can proceed with a true BM25\n"
        "    migration (supabase/migrations/20260729000011_bm25_search.sql).\n"
        "  - If it does not appear -> Phase 4 falls back to tuning ts_rank_cd\n"
        "    (normalization flags, custom text-search config) instead of an\n"
        "    engine swap. Document that decision in the migration header.\n"
    )


if __name__ == "__main__":
    main()

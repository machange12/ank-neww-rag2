"""
apply_migrations.py
===================
Apply the versioned SQL migrations under ``supabase/migrations/`` in
filename order to a Postgres database, recording each applied version
in ``public.schema_migrations`` so re-runs are idempotent.

This is the ONE documented migration path (Work package 1). The
legacy sources of truth — root ``schema.sql`` and ``sql/*.sql`` — are
deprecated and must NOT be applied manually (see sql/DEPRECATED.md).

Usage:
    python scripts/apply_migrations.py [--dsn POSTGRES_DSN] [--dry-run]

Environment:
    POSTGRES_DSN     postgresql://user:pass@host:port/db
                     (defaults to the value in config/`.env`)

Alternative deployment paths (documented in README):
  * Supabase CLI:   supabase link --project-ref <ref>; supabase db push
  * Supabase SQL Editor: paste the contents of each file in
    filename order (baseline first). The runner and CLI are the
    supported, repeatable paths; manual pasting is not.

Backup before applying to any database with existing data.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "supabase" / "migrations"
MIGRATIONS_TABLE = "public.schema_migrations"


def discover_migrations() -> list[Path]:
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        raise SystemExit(f"No .sql migrations found in {MIGRATIONS_DIR}")
    return files


def already_applied(conn, version: str) -> bool:
    cur = conn.execute(
        f"select 1 from {MIGRATIONS_TABLE} where version = %s",
        (version,),
    )
    return cur.fetchone() is not None


def ensure_table(conn) -> None:
    conn.execute(
        f"""
        create table if not exists {MIGRATIONS_TABLE}
        (
          version     text primary key,
          applied_at  timestamptz not null default now(),
          description text not null default ''
        )
        """
    )


def apply_migrations(dsn: str, dry_run: bool = False) -> None:
    import psycopg

    files = discover_migrations()
    print(f"Discovered {len(files)} migration(s) in {MIGRATIONS_DIR}")

    with psycopg.connect(dsn) as conn:
        ensure_table(conn)
        for path in files:
            version = path.stem.split("_", 1)[0]
            if already_applied(conn, version):
                print(f"  skip  {path.name}  (already applied)")
                continue
            sql = path.read_text(encoding="utf-8")
            if dry_run:
                print(f"  dry   {path.name}  ({len(sql)} bytes)")
                continue
            with conn.transaction():
                conn.execute(sql)
                conn.execute(
                    f"insert into {MIGRATIONS_TABLE} (version, description) values (%s, %s) on conflict (version) do nothing",
                    (version, path.name),
                )
            print(f"  ok    {path.name}")
    print("Migrations complete.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply versioned Supabase migrations.")
    parser.add_argument("--dsn", default=None, help="Postgres DSN (default: POSTGRES_DSN env / .env)")
    parser.add_argument("--dry-run", action="store_true", help="List migrations that would be applied without touching the DB.")
    args = parser.parse_args()

    dsn = args.dsn
    if not dsn:
        try:
            from config import settings
        except ImportError:
            settings = None
        if settings is not None and getattr(settings, "postgres_dsn", ""):
            dsn = settings.postgres_dsn
    if not dsn:
        print("No Postgres DSN provided. Use --dsn or set POSTGRES_DSN.", file=sys.stderr)
        return 2

    apply_migrations(dsn, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
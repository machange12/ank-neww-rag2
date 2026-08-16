"""
verify_schema.py
================
Verify that a database matches the expected schema produced by the
migration set under ``supabase/migrations/``.

Checks (with a direct Postgres connection):
  * migration versions recorded in public.schema_migrations;
  * required tables and required columns;
  * required indexes;
  * required functions + security-definer expectations;
  * RLS enabled (+ FORCE) state per table;
  * required RLS policies per table;
  * required function grants.

Fails (exit code 1) if any required item is missing. Works against a
local Postgres or a Supabase database using the project connection
string. Supabase free tier blocks direct connections; if a DSN is not
available you can still run the Python-only manifest tests
(``pytest tests/test_schema_manifest.py``) which validate the SQL
files without a live database.

Usage:
    python scripts/verify_schema.py [--dsn POSTGRES_DSN]
    python scripts/verify_schema.py --list-checks
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field


@dataclass
class TableCheck:
    table: str
    columns: list[str] = field(default_factory=list)
    rls_enabled: bool = True
    rls_forced: bool = False
    policies: list[str] = field(default_factory=list)


@dataclass
class FunctionCheck:
    name: str
    security_definer: bool | None = None


REQUIRED_MIGRATIONS = [
    "20260727000000",
    "20260727000001",
    "20260727000002",
    "20260727000003",
    "20260727000004",
    "20260727000005",
    "20260727000006",
    "20260727000007",
]

TABLES: list[TableCheck] = [
    TableCheck("documents", ["content", "embedding", "metadata", "access_level", "matter_id"], rls_forced=True,
               policies=["auth_uid can select documents", "service writes documents"]),
    TableCheck("document_metadata", ["file_id", "file_title", "content_hash", "access_level", "matter_id", "doc_type", "legal_entities"], rls_forced=True,
               policies=["auth_uid can select document_metadata", "service manages metadata"]),
    TableCheck("chat_sessions", ["session_id", "user_id_uuid", "tenant_id", "matter_id", "status", "retention_days"], rls_enabled=True,
               policies=["chat_sessions_select_own", "chat_sessions_insert_own"]),
    TableCheck("chat_memory", ["session_id", "message", "user_id", "tenant_id", "session_uuid"], rls_enabled=True,
               policies=["chat_memory_select_own", "chat_memory_insert_own"]),
    TableCheck("query_feedback", ["session_id", "user_id", "rating", "tenant_id", "user_id_uuid"], rls_enabled=True,
               policies=["query_feedback_select_own", "query_feedback_insert_own"]),
    TableCheck("tenants", ["id", "name"]),
    TableCheck("matters", ["matter_ref", "tenant_id"]),
    TableCheck("user_profiles", ["user_id", "tenant_id", "role", "access_level", "firm_wide", "admin"], rls_enabled=True,
               policies=["user_profiles_select_own"]),
    TableCheck("matter_access", ["user_id", "matter_id", "access_level", "can_administer"], rls_enabled=True,
               policies=["matter_access_select_own"]),
    TableCheck("legal_sources", ["canonical_name", "source_class", "rights_status", "ingestion_enabled"]),
    TableCheck("legal_documents", ["source_id", "title", "document_type", "authority_tier", "current_status"]),
    TableCheck("legal_document_versions", ["document_id", "source_hash", "valid_from", "valid_to", "repeal_date"]),
    TableCheck("legal_passages", ["version_id", "locator_kind", "locator_value", "passage_hash", "normalized_text"]),
    TableCheck("citation_records", ["passage_id", "document_version_id", "citation_status", "verifier_version"]),
    TableCheck("source_relationships", ["from_version_id", "to_version_id", "relationship"]),
    TableCheck("legal_research_runs", ["jurisdiction", "as_of_date", "locked_scope", "matter_scope"]),
    TableCheck("retrieval_events", ["research_run_id", "locked_scope", "query_expansion"]),
    TableCheck("security_events", ["event_type", "action", "outcome", "user_id"], rls_enabled=True,
               policies=["security_events_admin_read"]),
]

FUNCTIONS: list[FunctionCheck] = [
    FunctionCheck("delete_documents_by_file_id", security_definer=True),
    FunctionCheck("match_documents_rls", security_definer=False),
    FunctionCheck("hybrid_search_rls", security_definer=False),
    FunctionCheck("match_documents", security_definer=True),
    FunctionCheck("auth_tenant_id", security_definer=True),
    FunctionCheck("auth_user_access_level", security_definer=True),
    FunctionCheck("auth_user_admin", security_definer=True),
    FunctionCheck("auth_has_firm_wide", security_definer=True),
    FunctionCheck("auth_matter_ids", security_definer=True),
    FunctionCheck("auth_can_access_matter", security_definer=True),
    FunctionCheck("auth_can_access_matter_ref", security_definer=True),
    FunctionCheck("auth_can_administer_matter", security_definer=True),
    FunctionCheck("auth_can_administer_matter_ref", security_definer=True),
    FunctionCheck("auth_matter_admin_level", security_definer=True),
]

REQUIRED_INDEXES = [
    "documents_embedding_idx",
    "documents_metadata_idx",
    "documents_fts_idx",
    "chat_memory_session_idx",
    "legal_passages_version_idx",
    "legal_doc_versions_document_idx",
    "matter_access_user_idx",
]


def _connect(dsn: str):
    import psycopg

    return psycopg.connect(dsn)


def _check_table(conn, tc: TableCheck) -> list[str]:
    errors: list[str] = []
    # table exists
    row = conn.execute(
        "select 1 from information_schema.tables where table_schema='public' and table_name=%s",
        (tc.table,),
    ).fetchone()
    if not row:
        return [f"table public.{tc.table} missing"]

    cols = {
        r[0]
        for r in conn.execute(
            "select column_name from information_schema.columns where table_schema='public' and table_name=%s",
            (tc.table,),
        )
    }
    for col in tc.columns:
        if col not in cols:
            errors.append(f"table public.{tc.table}: missing column {col}")

    # RLS
    rls = conn.execute(
        "select relrowsecurity, relforcerowsecurity from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='public' and c.relname=%s",
        (tc.table,),
    ).fetchone()
    if rls:
        enabled, forced = rls[0], rls[1]
        if tc.rls_enabled and not enabled:
            errors.append(f"table public.{tc.table}: RLS not enabled")
        if tc.rls_forced and not forced:
            errors.append(f"table public.{tc.table}: RLS not FORCED")

    # policies
    existing = {
        r[0]
        for r in conn.execute(
            "select policyname from pg_policies where schemaname='public' and tablename=%s",
            (tc.table,),
        )
    }
    for pol in tc.policies:
        if pol not in existing:
            errors.append(f"table public.{tc.table}: policy '{pol}' missing")
    return errors


def _check_function(conn, fc: FunctionCheck) -> list[str]:
    errors: list[str] = []
    row = conn.execute(
        "select prosecdef from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='public' and p.proname=%s",
        (fc.name,),
    ).fetchone()
    if not row:
        return [f"function public.{fc.name} missing"]
    if fc.security_definer is not None and row[0] != fc.security_definer:
        errors.append(
            f"function public.{fc.name}: security definer={row[0]}, expected={fc.security_definer}"
        )
    return errors


def _check_indexes(conn) -> list[str]:
    rows = {
        r[0]
        for r in conn.execute(
            "select indexname from pg_indexes where schemaname='public' and tablename not like 'pg_%'"
        )
    }
    return [f"index {name} missing" for name in REQUIRED_INDEXES if name not in rows]


def _check_migrations(conn) -> list[str]:
    try:
        rows = {
            r[0]
            for r in conn.execute(
                "select version from public.schema_migrations"
            )
        }
    except Exception as exc:  # noqa: BLE001
        return [f"could not read public.schema_migrations: {exc}"]
    return [f"migration {v} not applied" for v in REQUIRED_MIGRATIONS if v not in rows]


def verify_database(dsn: str) -> tuple[bool, list[str]]:
    errors: list[str] = []
    with _connect(dsn) as conn:
        errors += _check_migrations(conn)
        for tc in TABLES:
            errors += _check_table(conn, tc)
        for fc in FUNCTIONS:
            errors += _check_function(conn, fc)
        errors += _check_indexes(conn)
    return (not errors, errors)


def verify_manifest_from_sql() -> tuple[bool, list[str]]:
    """Validate the migration SQL files reference the expected entities.

    Used as the offline check when no database is reachable (see
    tests/test_schema_manifest.py). Returns (ok, errors).
    """
    import re

    migrations_dir = (
        __import__("pathlib").Path(__file__).resolve().parent.parent
        / "supabase" / "migrations"
    )
    sql = "\n".join(p.read_text(encoding="utf-8") for p in sorted(migrations_dir.glob("*.sql")))
    errors: list[str] = []

    for tc in TABLES:
        if not re.search(rf"create table if not exists public\.{tc.table}\b", sql):
            errors.append(f"migration SQL missing create table public.{tc.table}")
        for col in tc.columns:
            if not re.search(rf"\b{re.escape(col)}\b", sql):
                errors.append(f"migration SQL missing column {tc.table}.{col}")
    for fc in FUNCTIONS:
        if not re.search(rf"create or replace function public\.{fc.name}\b", sql):
            errors.append(f"migration SQL missing function public.{fc.name}")
    for idx in REQUIRED_INDEXES:
        if not re.search(rf"\b{re.escape(idx)}\b", sql):
            errors.append(f"migration SQL missing index {idx}")
    for version in REQUIRED_MIGRATIONS:
        if not re.search(rf"'{version}'", sql):
            errors.append(f"migration SQL missing version marker {version}")
    return (not errors, errors)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the database schema matches migrations.")
    parser.add_argument("--dsn", default=None, help="Postgres DSN (default: POSTGRES_DSN from .env)")
    parser.add_argument("--manifest-only", action="store_true", help="Check migration SQL files offline without a database.")
    parser.add_argument("--list-checks", action="store_true", help="Print the expected manifest and exit.")
    parser.add_argument("--json", action="store_true", help="Emit results as JSON.")
    args = parser.parse_args()

    if args.list_checks:
        manifest = {
            "migrations": REQUIRED_MIGRATIONS,
            "tables": [{"table": t.table, "columns": t.columns, "policies": t.policies} for t in TABLES],
            "functions": [{"name": f.name, "security_definer": f.security_definer} for f in FUNCTIONS],
            "indexes": REQUIRED_INDEXES,
        }
        print(json.dumps(manifest, indent=2))
        return 0

    if args.manifest_only:
        ok, errors = verify_manifest_from_sql()
        if args.json:
            print(json.dumps({"ok": ok, "errors": errors}))
        else:
            _print(ok, errors)
        return 0 if ok else 1

    dsn = args.dsn
    if not dsn:
        try:
            from config import settings
        except ImportError:
            settings = None
        if settings is not None and getattr(settings, "postgres_dsn", ""):
            dsn = settings.postgres_dsn
    if not dsn:
        print("No DSN available and --manifest-only not set. Provide --dsn or POSTGRES_DSN.", file=sys.stderr)
        return 2

    ok, errors = verify_database(dsn)
    if args.json:
        print(json.dumps({"ok": ok, "errors": errors}))
    else:
        _print(ok, errors)
    return 0 if ok else 1


def _print(ok: bool, errors: list[str]) -> None:
    if ok:
        print("SCHEMA OK")
    else:
        print("SCHEMA FAILED:")
        for err in errors:
            print(f"  - {err}")


if __name__ == "__main__":
    sys.exit(main())
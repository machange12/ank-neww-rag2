"""Offline schema-manifest validation (no live database required)."""
from __future__ import annotations

from scripts.verify_schema import REQUIRED_MIGRATIONS, verify_manifest_from_sql


def test_manifest_validates_offline():
    ok, errors = verify_manifest_from_sql()
    assert ok, f"migration SQL missing required schema items: {errors}"


def test_all_required_migrations_present():
    from pathlib import Path

    migrations_dir = Path(__file__).resolve().parent.parent / "supabase" / "migrations"
    files = {p.stem.split("_", 1)[0] for p in migrations_dir.glob("*.sql")}
    for version in REQUIRED_MIGRATIONS:
        assert version in files, f"migration file missing for {version}"


def test_required_ddl_present_in_sql():
    """Assert the SQL files contain the critical DDL the manifest expects."""
    import re

    from pathlib import Path

    migrations_dir = Path(__file__).resolve().parent.parent / "supabase" / "migrations"
    sql = "\n".join(p.read_text(encoding="utf-8") for p in sorted(migrations_dir.glob("*.sql")))

    required_ddl = [
        r"create table if not exists public\.legal_sources",
        r"create table if not exists public\.legal_documents",
        r"create table if not exists public\.legal_document_versions",
        r"create table if not exists public\.legal_passages",
        r"create table if not exists public\.citation_records",
        r"create table if not exists public\.source_relationships",
        r"create table if not exists public\.user_profiles",
        r"create table if not exists public\.matter_access",
        r"create table if not exists public\.security_events",
        r"create or replace function public\.auth_can_administer_matter_ref",
        r"create or replace function public\.auth_user_admin",
        r"alter table public\.documents force row level security",
        r"create policy \"auth_uid can select documents\"",
    ]
    for pattern in required_ddl:
        assert re.search(pattern, sql), f"required DDL missing: {pattern}"
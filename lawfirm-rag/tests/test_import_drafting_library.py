"""Tests for scripts/import_drafting_library.py (offline; no Supabase, no LLM).

The importer turns a folder of the firm's own documents + a manifest into the
drafting-library seed shape. These tests cover the manifest/folder machinery
only: scaffolding, default merging, body resolution and the error cases an
operator is most likely to hit.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.import_drafting_library import (
    DRY_RUN_OUT,
    _mime_for,
    _scaffold,
    build_seed_data,
)


def _write(folder, name, text):
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_scaffold_lists_documents_and_refuses_to_overwrite(tmp_path):
    _write(tmp_path, "clauses/force-majeure.md", "Force majeure clause body text.")
    _write(tmp_path, "clauses/governing-law.txt", "Governing law clause body text.")
    _write(tmp_path, "notes/ignore-me.json", "{}")

    out = _scaffold(tmp_path)
    manifest = json.loads(out.read_text(encoding="utf-8"))

    assert manifest["_comment"]
    assert manifest["defaults"]["access_level"] == 1
    files = [c["file"] for c in manifest["clauses"]]
    assert files == ["clauses/force-majeure.md", "clauses/governing-law.txt"]
    assert manifest["clauses"][0]["title"] == "force majeure"
    assert manifest["templates"] == [] and manifest["playbooks"] == []

    # Re-scaffolding must never clobber edited metadata.
    with pytest.raises(SystemExit):
        _scaffold(tmp_path)


def test_build_seed_data_resolves_files_and_merges_defaults(tmp_path):
    _write(tmp_path, "clauses/force-majeure.md", "Neither party shall be liable for force majeure events.")

    manifest = {
        "defaults": {"access_level": 3, "matter_id": "", "jurisdiction": "ke"},
        "templates": [{"title": "SPA", "doc_type": "share_purchase", "skeleton": [{"heading": "Definitions"}]}],
        "clauses": [
            {"title": "Force majeure", "clause_type": "force_majeure", "file": "clauses/force-majeure.md"},
            {"title": "Entire agreement", "body": "  This agreement is the entire agreement.  ", "access_level": 1},
        ],
        "playbooks": [{"name": "SPA rules", "rules": [{"clause_type": "indemnity", "rule_text": "Cap at fees."}]}],
    }

    seed = build_seed_data(manifest, tmp_path)

    assert seed["templates"][0]["access_level"] == 3
    assert seed["templates"][0]["jurisdiction"] == "ke"
    assert seed["playbooks"][0]["access_level"] == 3

    force_majeure, entire = seed["clauses"]
    assert force_majeure["body"].startswith("Neither party")
    assert force_majeure["access_level"] == 3        # from defaults
    assert "file" not in force_majeure               # resolved away
    assert entire["body"] == "This agreement is the entire agreement."
    assert entire["access_level"] == 1               # entry overrides defaults


def test_build_seed_data_rejects_a_clause_with_no_content(tmp_path):
    with pytest.raises(ValueError, match="needs 'body' or 'file'"):
        build_seed_data({"clauses": [{"title": "Empty"}]}, tmp_path)


def test_build_seed_data_rejects_a_missing_file(tmp_path):
    with pytest.raises(ValueError, match="file not found"):
        build_seed_data({"clauses": [{"title": "Gone", "file": "clauses/nope.md"}]}, tmp_path)


def test_empty_manifest_is_valid(tmp_path):
    seed = build_seed_data({}, tmp_path)
    assert seed == {"templates": [], "clauses": [], "playbooks": []}


def test_dry_run_output_path_is_inside_the_repo():
    assert DRY_RUN_OUT.name == "imported_seed_data.json"
    assert DRY_RUN_OUT.parent.name == "drafting"


def test_mime_types_cover_the_supported_suffixes():
    assert _mime_for(Path("force-majeure.docx")) == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert _mime_for(Path("clause.md")) == "text/plain"
    assert _mime_for(Path("clause.markdown")) == "text/plain"
    assert _mime_for(Path("book.xlsx")) == "application/octet-stream"

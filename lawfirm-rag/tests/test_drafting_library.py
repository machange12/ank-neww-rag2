"""Offline tests for the curated drafting-library loader (no live DB / model).

``drafting/library.py`` is the service-role curation write path. These tests
exercise it against the in-memory ``FakeClient`` with a stubbed embedder, so the
idempotent delete-then-insert behaviour and the normalized row shapes are
verified without a database or the real embedding model.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import FakeClient

import drafting.library as lib

SEED_FILE = Path(lib.__file__).resolve().parent / "seed_data.json"


class _FakeEmbedder:
    """Records calls and returns fixed-width vectors (no model load)."""

    def __init__(self) -> None:
        self.calls = 0
        self.texts: list[str] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        self.texts = list(texts)
        return [[0.5] * 8 for _ in texts]


@pytest.fixture
def stub_embedder(monkeypatch):
    embedder = _FakeEmbedder()
    monkeypatch.setattr(lib, "get_bulk_embeddings", lambda: embedder)
    monkeypatch.setattr(lib, "embed_prefixes", lambda: ("query: ", "passage: "))
    return embedder


def _tables(client: FakeClient, name: str) -> list[dict]:
    return client._tables.get(name, [])


def test_clause_embed_text_includes_prefix_and_identifying_fields(stub_embedder):
    text = lib._clause_embed_text(
        {"title": "Governing law", "clause_type": "governing_law", "body": "Kenyan law applies."}
    )
    assert text.startswith("passage: ")
    assert "Clause: Governing law" in text
    assert "Type: governing_law" in text
    assert "Kenyan law applies." in text


def test_seed_templates_writes_normalized_defaults(stub_embedder):
    client = FakeClient()
    count = lib.seed_templates(
        client,
        [{"title": "Mutual NDA", "doc_type": "nda", "skeleton": [{"heading": "Parties"}]}],
        tenant_id="t1",
    )
    assert count == 1
    (row,) = _tables(client, "draft_templates")
    assert row["tenant_id"] == "t1"
    assert row["jurisdiction"] == "ke"
    assert row["access_level"] == 1
    assert row["matter_id"] == ""
    assert row["skeleton"] == [{"heading": "Parties"}]


def test_seed_templates_is_idempotent_by_title(stub_embedder):
    client = FakeClient(tables={"draft_templates": [{"title": "Mutual NDA", "doc_type": "old"}]})
    lib.seed_templates(client, [{"title": "Mutual NDA", "doc_type": "nda"}], None)
    rows = [r for r in _tables(client, "draft_templates") if r["title"] == "Mutual NDA"]
    assert len(rows) == 1
    assert rows[0]["doc_type"] == "nda"


def test_seed_clauses_embeds_and_stores_vectors(stub_embedder):
    client = FakeClient()
    count = lib.seed_clauses(
        client,
        [{"title": "Governing law", "clause_type": "governing_law", "body": "Kenyan law."}],
        tenant_id=None,
    )
    assert count == 1
    assert stub_embedder.calls == 1
    (row,) = _tables(client, "draft_clauses")
    assert row["body"] == "Kenyan law."
    assert row["is_standard"] is True
    assert row["embedding"] == [0.5] * 8


def test_seed_clauses_empty_does_not_embed(stub_embedder):
    client = FakeClient()
    assert lib.seed_clauses(client, [], None) == 0
    assert stub_embedder.calls == 0


def test_seed_playbooks_inserts_rules_in_order(stub_embedder):
    client = FakeClient()
    count = lib.seed_playbooks(
        client,
        [
            {
                "name": "Inbound NDA triage",
                "doc_type": "nda",
                "rules": [
                    {"clause_type": "governing_law", "position": "preferred", "rule_text": "Kenya.", "severity": "medium"},
                    {"clause_type": "non_solicit", "position": "prohibited", "rule_text": "No non-solicit.", "severity": "high"},
                ],
            }
        ],
        tenant_id=None,
    )
    assert count == 1
    (playbook,) = _tables(client, "playbooks")
    rules = sorted(_tables(client, "playbook_rules"), key=lambda r: r["order_index"])
    assert len(rules) == 2
    assert [r["order_index"] for r in rules] == [0, 1]
    assert all(r["playbook_id"] == playbook["id"] for r in rules)
    assert rules[1]["position"] == "prohibited"


def test_seed_library_summarizes_counts(stub_embedder):
    client = FakeClient()
    summary = lib.seed_library(
        client,
        {
            "templates": [{"title": "T1"}],
            "clauses": [{"title": "C1", "body": "b"}],
            "playbooks": [{"name": "P1", "rules": []}],
        },
        tenant_id=None,
    )
    assert summary == {"templates": 1, "clauses": 1, "playbooks": 1}


def test_seed_data_file_is_well_formed():
    data = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    assert {"templates", "clauses", "playbooks"} <= set(data)

    doc_types = {"nda", "lease", "engagement_letter", "memo"}
    for tpl in data["templates"]:
        assert tpl["title"] and tpl["doc_type"] in doc_types
        assert isinstance(tpl.get("skeleton"), list) and tpl["skeleton"]
        for section in tpl["skeleton"]:
            assert section["heading"]
            assert section["clause_type"]

    for clause in data["clauses"]:
        # Every clause must be embeddable: title + body, and a clause_type.
        assert clause["title"] and clause["body"] and clause["clause_type"]
        assert clause.get("doc_type") in {"", *doc_types}

    for playbook in data["playbooks"]:
        assert playbook["name"] and playbook["doc_type"] in doc_types
        assert playbook.get("rules")
        for rule in playbook["rules"]:
            assert rule["rule_text"]
            assert rule["position"] in {"preferred", "required", "prohibited", "advisory"}
            assert rule["severity"] in {"high", "medium", "low"}

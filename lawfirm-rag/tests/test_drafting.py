"""Offline tests for the drafting assistant (Draft Studio).

Deterministic, no live DB, no network. Exercise the pure skeleton/query
helpers, the per-section no-source guard, citation hygiene, and draft
ownership via the fake PostgREST client.
"""
from __future__ import annotations

import asyncio
from typing import Any

from conftest import FakeClient

from agents import draft_agent
from drafting import store


def _run(coro):
    return asyncio.run(coro)


class _FakeLLM:
    def __init__(self, reply: str) -> None:
        self.reply = reply

    async def ainvoke(self, messages: Any):
        class _R:
            content = self.reply

        return _R()


class _FakeRetriever:
    def __init__(self, docs: list[Any]) -> None:
        self._docs = docs

    async def ainvoke(self, query: str):
        return self._docs


def _doc(content: str, file_id: str = "f1", title: str = "Precedent") -> Any:
    from langchain_core.documents import Document

    return Document(page_content=content, metadata={"file_id": file_id, "file_title": title})


# ---------------------------------------------------------------- skeleton

def test_default_skeleton_for_known_and_unknown_types():
    nda = draft_agent.default_skeleton("NDA")
    assert nda and all("heading" in s for s in nda)
    unknown = draft_agent.default_skeleton("moon agreement")
    assert unknown and unknown[0]["heading"] == "Parties"


def test_parse_skeleton_handles_prose_and_fences():
    fallback = draft_agent.default_skeleton("memo")
    raw = 'Here you go:\n```json\n[{"heading": "Issue", "clause_type": "issue"}]\n```'
    parsed = draft_agent.parse_skeleton(raw, fallback)
    assert len(parsed) == 1
    assert parsed[0]["heading"] == "Issue"
    assert parsed[0]["required"] is True


def test_parse_skeleton_falls_back_on_garbage():
    fallback = draft_agent.default_skeleton("memo")
    assert draft_agent.parse_skeleton("not json at all", fallback) is fallback
    assert draft_agent.parse_skeleton("[{bad}]", fallback) is fallback


def test_parse_skeleton_drops_items_without_heading():
    fallback = draft_agent.default_skeleton("memo")
    parsed = draft_agent.parse_skeleton('[{"clause_type": "x"}, {"heading": "Good"}]', fallback)
    assert [s["heading"] for s in parsed] == ["Good"]


# ---------------------------------------------------------------- helpers

def test_format_brief_dict_and_string():
    assert draft_agent.format_brief(None) == ""
    assert draft_agent.format_brief("just text") == "just text"
    text = draft_agent.format_brief({"governing_law": "Kenya", "term_years": 5, "skip": ""})
    assert "governing law: Kenya" in text
    assert "term years: 5" in text
    assert "skip" not in text


def test_build_section_query_includes_parts():
    section = {"heading": "Governing Law", "clause_type": "governing_law", "guidance": "Pick law"}
    query = draft_agent.build_section_query(section, "Jurisdiction: Kenya")
    assert "Governing Law" in query
    assert "governing law" in query
    assert "Pick law" in query
    assert "Jurisdiction: Kenya" in query


def test_is_no_source_answer():
    assert draft_agent.is_no_source_answer(draft_agent.NO_SOURCE_SECTION)
    assert draft_agent.is_no_source_answer("  no source: whatever")
    assert not draft_agent.is_no_source_answer("The parties agree as follows [1].")


# ---------------------------------------------------------------- guard

def test_generate_section_needs_source_when_nothing_retrieved(monkeypatch):
    monkeypatch.setattr(draft_agent, "_make_retriever", lambda *a, **k: (_FakeRetriever([]), None))
    monkeypatch.setattr(draft_agent, "search_clauses", lambda *a, **k: [])
    # LLM must not be called on the no-source path; fail loudly if it is.
    llm = _FakeLLM("SHOULD NOT BE USED")
    result = _run(draft_agent.generate_section(
        {"heading": "Governing Law", "clause_type": "governing_law"},
        brief_text="",
        doc_type="nda",
        user_client=FakeClient(),
        llm=llm,
    ))
    assert result["status"] == "needs_source"
    assert result["sources"] == []
    assert result["content"] == draft_agent.NO_SOURCE_SECTION


def test_generate_section_grounds_and_cites(monkeypatch):
    monkeypatch.setattr(
        draft_agent, "_make_retriever",
        lambda *a, **k: (_FakeRetriever([_doc("Clause: Kenyan law governs.", "f1", "Lease")]), None),
    )
    monkeypatch.setattr(draft_agent, "search_clauses", lambda *a, **k: [])
    llm = _FakeLLM("This agreement is governed by Kenyan law [1].")
    result = _run(draft_agent.generate_section(
        {"heading": "Governing Law", "clause_type": "governing_law"},
        brief_text="",
        doc_type="lease",
        user_client=FakeClient(),
        llm=llm,
    ))
    assert result["status"] == "generated"
    assert result["sources"] and result["sources"][0]["n"] == 1
    assert "[1]" in result["content"]


def test_finish_section_strips_out_of_range_citations():
    sources = [{"n": 1, "key": "f1", "title": "Precedent", "file_title": "Precedent"}]
    result = draft_agent._finish_section("Grounded point [1] and invented point [9].", sources)
    assert result["status"] == "generated"
    assert "[1]" in result["content"]
    assert "[9]" not in result["content"]


def test_finish_section_no_source_marker_empties_sources():
    result = draft_agent._finish_section(draft_agent.NO_SOURCE_SECTION, [{"n": 1}])
    assert result["status"] == "needs_source"
    assert result["sources"] == []


# ---------------------------------------------------------------- store

def test_create_and_get_draft_round_trip():
    client = FakeClient(auth_uid="user-1")
    draft = _run(store.create_draft(
        client,
        user_id="user-1",
        tenant_id="t1",
        title="Mutunga NDA",
        doc_type="nda",
        brief={"governing_law": "Kenya"},
        skeleton=[{"heading": "Parties", "clause_type": "parties"}, {"heading": "Term", "clause_type": "term"}],
    ))
    assert draft["id"]
    assert len(draft["sections"]) == 2

    fetched = _run(store.get_draft(client, draft["id"]))
    assert fetched is not None
    assert [s["order_index"] for s in fetched["sections"]] == [0, 1]
    # A baseline v1 snapshot is written on create.
    assert [v["version"] for v in fetched["versions"]] == [1]
    assert fetched["current_version"] == 1


def test_draft_is_owner_scoped():
    owner = FakeClient(auth_uid="user-1")
    draft = _run(store.create_draft(
        owner,
        user_id="user-1",
        tenant_id="t1",
        title="Private draft",
        doc_type="memo",
        skeleton=[{"heading": "Issue", "clause_type": "issue"}],
    ))
    intruder = FakeClient(auth_uid="user-2")
    intruder._tables = owner._tables  # share the underlying store
    assert _run(store.get_draft(intruder, draft["id"])) is None
    assert _run(store.list_drafts(intruder)) == []


def _make_draft(client, **kwargs):
    defaults = dict(
        user_id="user-1",
        tenant_id="t1",
        title="D",
        doc_type="nda",
        skeleton=[{"heading": "Parties", "clause_type": "parties"}],
    )
    defaults.update(kwargs)
    return _run(store.create_draft(client, **defaults))


def test_save_version_increments_and_bumps_current():
    client = FakeClient(auth_uid="user-1")
    draft = _make_draft(client)
    _run(store.update_section(client, draft["id"], 0, content="First text [1].", status="generated"))

    saved = _run(store.snapshot_version(client, _run(store.get_draft(client, draft["id"])), user_id="user-1", label="manual"))
    assert saved is not None
    assert saved["version"] == 2

    fetched = _run(store.get_draft(client, draft["id"]))
    assert fetched["current_version"] == 2
    assert [v["version"] for v in fetched["versions"]] == [2, 1]


def test_restore_version_round_trip_append_only():
    client = FakeClient(auth_uid="user-1")
    draft = _make_draft(client)
    _run(store.update_section(client, draft["id"], 0, content="First version text.", status="edited"))
    _run(store.snapshot_version(client, _run(store.get_draft(client, draft["id"])), user_id="user-1"))

    # Diverge from the snapshot, then restore it.
    _run(store.update_section(client, draft["id"], 0, content="Second version text.", status="edited"))
    restored = _run(store.restore_version(client, _run(store.get_draft(client, draft["id"])), 2, user_id="user-1"))
    assert restored is not None
    assert restored["sections"][0]["content"] == "First version text."
    # Restore is recorded as a new version, not a rewrite of history.
    assert [v["version"] for v in restored["versions"]] == [3, 2, 1]
    assert restored["current_version"] == 3


def test_restore_missing_version_returns_none():
    client = FakeClient(auth_uid="user-1")
    draft = _make_draft(client)
    assert _run(store.restore_version(client, draft, 99, user_id="user-1")) is None


def test_update_section_sets_edited_status():
    client = FakeClient(auth_uid="user-1")
    draft = _run(store.create_draft(
        client,
        user_id="user-1",
        tenant_id="t1",
        title="D",
        doc_type="nda",
        skeleton=[{"heading": "Parties", "clause_type": "parties"}],
    ))
    saved = _run(store.update_section(
        client, draft["id"], 0, content="The parties are X and Y.", status="edited"
    ))
    assert saved is not None
    fetched = _run(store.get_draft(client, draft["id"]))
    assert fetched["sections"][0]["content"] == "The parties are X and Y."
    assert fetched["sections"][0]["status"] == "edited"

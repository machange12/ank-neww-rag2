"""Offline tests for the drafting assistant (Draft Studio).

Deterministic, no live DB, no network. Exercise the pure skeleton/query
helpers, the per-section no-source guard, citation hygiene, and draft
ownership via the fake PostgREST client.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
from conftest import FakeClient
from fastapi import HTTPException

from agents import draft_agent
from drafting import store
from routers import draft as draft_router


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


class _BoomLLM:
    """Fails the test if any LLM path is reached."""

    async def ainvoke(self, messages: Any):
        raise AssertionError("LLM must not be called")

    async def astream(self, messages: Any):
        raise AssertionError("LLM must not be called")
        yield  # pragma: no cover


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


# ---------------------------------------------------------------- refine

def test_refine_grounded_section_keeps_sources():
    sources = [{"n": 1, "key": "f1", "title": "Precedent"}]
    section = {
        "heading": "Term",
        "content": "The term is five years [1].",
        "sources": sources,
        "status": "generated",
    }
    out = _run(draft_agent.refine_section(
        section, "tighten the language", FakeClient(), _FakeLLM("The term is 5 years [1].")
    ))
    assert out["status"] == "edited"
    assert "[1]" in out["content"]
    assert out["sources"] == sources


def test_refine_refuses_ungrounded_section_without_calling_llm():
    # The no-source guard must cover refine too: a needs_source section carries
    # a non-empty notice, so only an explicit guard stops the LLM being asked to
    # fill a gap (ADR-0006: "the LLM is never asked to fill that gap").
    section = {
        "heading": "Governing Law",
        "clause_type": "governing_law",
        "content": draft_agent.NO_SOURCE_SECTION,
        "sources": [],
        "status": "needs_source",
    }
    with pytest.raises(ValueError):
        _run(draft_agent.refine_section(section, "tighten", FakeClient(), _BoomLLM()))


def _patch_auth(monkeypatch, user_id, client):
    monkeypatch.setattr(
        draft_router, "_auth", lambda token: ({"user_id": user_id, "email": "u@x"}, client)
    )


def test_refine_endpoint_422_on_needs_source_section(monkeypatch):
    client = FakeClient(auth_uid="user-1")
    draft = _run(store.create_draft(
        client,
        user_id="user-1",
        tenant_id="t1",
        title="D",
        doc_type="nda",
        skeleton=[{"heading": "Governing Law", "clause_type": "governing_law"}],
    ))
    _run(store.update_section(
        client, draft["id"], 0,
        content=draft_agent.NO_SOURCE_SECTION, sources=[], status="needs_source",
    ))
    _patch_auth(monkeypatch, "user-1", client)
    monkeypatch.setattr(draft_router, "make_chat_llm", lambda streaming=False: _BoomLLM())

    with pytest.raises(HTTPException) as exc:
        _run(draft_router.refine_section_endpoint(
            draft["id"], 0, draft_router.RefineBody(instruction="tighten"), None
        ))
    assert exc.value.status_code == 422


def test_check_endpoint_404_for_unknown_playbook(monkeypatch):
    client = FakeClient(auth_uid="user-1")
    draft = _run(store.create_draft(
        client,
        user_id="user-1",
        tenant_id="t1",
        title="D",
        doc_type="nda",
        skeleton=[{"heading": "Parties", "clause_type": "parties"}],
    ))
    _patch_auth(monkeypatch, "user-1", client)

    # An explicit playbook_id that is not available to the caller is a 404, not
    # a misleading "no playbook applies to this document type" 200.
    with pytest.raises(HTTPException) as exc:
        _run(draft_router.check_draft_endpoint(
            draft["id"], draft_router.CheckBody(playbook_id="pb-missing"), None
        ))
    assert exc.value.status_code == 404


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


def test_section_guidance_and_required_persist_through_reload():
    # Migration 00015: the skeleton's guidance hint and required flag used to be
    # attached only to the create response, so they vanished on reload.
    client = FakeClient(auth_uid="user-1")
    draft = _run(store.create_draft(
        client,
        user_id="user-1",
        tenant_id="t1",
        title="Hints",
        doc_type="nda",
        skeleton=[
            {"heading": "Parties", "clause_type": "parties", "guidance": "Name the parties.", "required": True},
            {"heading": "Permitted Disclosures", "clause_type": "permitted_disclosure", "guidance": "Carve-outs.", "required": False},
        ],
    ))
    fetched = _run(store.get_draft(client, draft["id"]))
    by_index = {s["order_index"]: s for s in fetched["sections"]}
    assert by_index[0]["guidance"] == "Name the parties."
    assert by_index[0]["required"] is True
    assert by_index[1]["guidance"] == "Carve-outs."
    assert by_index[1]["required"] is False


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

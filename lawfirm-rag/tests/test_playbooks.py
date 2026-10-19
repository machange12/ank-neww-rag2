"""Offline tests for playbook checks (Draft Studio review pass).

Deterministic, no live DB, no network. Exercise the pure verdict->flag mapping
(including the prohibited-position breach path), the LLM wrapper with a fake
model, and playbook persistence via the fake PostgREST client.
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
        self.calls = 0

    async def ainvoke(self, messages: Any):
        self.calls += 1

        class _R:
            content = self.reply

        return _R()


RULES = [
    {"id": "r1", "clause_type": "confidentiality", "position": "required", "rule_text": "Mutual.", "severity": "high"},
    {"id": "r2", "clause_type": "term", "position": "preferred", "rule_text": "<= 5 years.", "severity": "medium"},
]


# ---------------------------------------------------------------- mapping

def test_map_rule_verdicts_attaches_metadata_and_defaults_unclear():
    verdicts = [{"rule_id": "r1", "order_index": 0, "status": "ok", "note": "mutual"}]
    flags = draft_agent.map_rule_verdicts(RULES, verdicts)
    assert [f["rule_id"] for f in flags] == ["r1", "r2"]  # every rule reported, in order
    assert flags[0]["status"] == "ok"
    assert flags[0]["position"] == "required"
    assert flags[0]["severity"] == "high"
    assert flags[1]["status"] == "unclear"  # no verdict -> unclear, never dropped
    assert flags[1]["rule_text"] == "<= 5 years."


def test_map_rule_verdicts_coerces_invalid_status():
    verdicts = [{"rule_id": "r1", "status": "maybe"}]
    flags = draft_agent.map_rule_verdicts(RULES, verdicts)
    assert flags[0]["status"] == "unclear"


def test_map_rule_verdicts_positional_fallback_when_no_ids():
    verdicts = [{"status": "ok"}, {"status": "breach", "order_index": 3}]
    flags = draft_agent.map_rule_verdicts(RULES, verdicts)
    assert flags[0]["status"] == "ok"
    assert flags[1]["status"] == "breach"
    assert flags[1]["order_index"] == 3


def test_prohibited_position_can_be_a_breach():
    rules = [{"id": "p1", "clause_type": "non_solicit", "position": "prohibited",
              "rule_text": "No non-solicitation.", "severity": "high"}]
    verdicts = [{"rule_id": "p1", "order_index": 4, "status": "breach", "note": "clause present"}]
    flags = draft_agent.map_rule_verdicts(rules, verdicts)
    assert flags[0]["position"] == "prohibited"
    assert flags[0]["status"] == "breach"
    assert flags[0]["severity"] == "high"
    assert flags[0]["order_index"] == 4


def test_parse_verdicts_tolerates_prose_and_fences():
    raw = 'Here:\n```json\n[{"rule_id": "r1", "status": "ok"}]\n```'
    parsed = draft_agent.parse_verdicts(raw)
    assert parsed == [{"rule_id": "r1", "status": "ok"}]
    assert draft_agent.parse_verdicts("no json") == []


def test_summarize_flags_counts_status_and_breaches_by_severity():
    flags = [
        {"status": "ok", "severity": "low"},
        {"status": "breach", "severity": "high"},
        {"status": "breach", "severity": "high"},
        {"status": "unclear", "severity": "medium"},
    ]
    summary = draft_agent.summarize_flags(flags)
    assert summary["total"] == 4
    assert summary["ok"] == 1
    assert summary["breach"] == 2
    assert summary["unclear"] == 1
    assert summary["breach_high"] == 2


# ---------------------------------------------------------------- agent

def test_check_playbook_uses_one_call_and_maps_rules():
    llm = _FakeLLM('[{"rule_id": "r1", "order_index": 0, "status": "ok"}, '
                   '{"rule_id": "r2", "order_index": 5, "status": "breach", "note": "long"}]')
    flags = _run(draft_agent.check_playbook(RULES, [{"order_index": 0, "heading": "Term", "content": "text"}], llm))
    assert llm.calls == 1
    assert [f["status"] for f in flags] == ["ok", "breach"]


def test_check_playbook_no_rules_skips_llm():
    llm = _FakeLLM("[]")
    assert _run(draft_agent.check_playbook([], [], llm)) == []
    assert llm.calls == 0


def test_check_playbook_llm_failure_yields_all_unclear():
    class _BoomLLM:
        async def ainvoke(self, messages: Any):
            raise RuntimeError("provider down")

    flags = _run(draft_agent.check_playbook(RULES, [], _BoomLLM()))
    assert len(flags) == 2
    assert all(f["status"] == "unclear" for f in flags)


# ---------------------------------------------------------------- store

def _client_with_playbooks() -> FakeClient:
    return FakeClient(tables={
        "playbooks": [
            {"id": "pb-nda", "name": "NDA triage", "doc_type": "nda", "is_active": True, "access_level": 1, "matter_id": "", "description": ""},
            {"id": "pb-lease", "name": "Lease review", "doc_type": "lease", "is_active": True, "access_level": 1, "matter_id": "", "description": ""},
            {"id": "pb-global", "name": "Global", "doc_type": "", "is_active": True, "access_level": 1, "matter_id": "", "description": ""},
            {"id": "pb-off", "name": "Retired", "doc_type": "nda", "is_active": False, "access_level": 1, "matter_id": "", "description": ""},
        ],
        "playbook_rules": [
            {"id": "rule-2", "playbook_id": "pb-nda", "order_index": 1, "clause_type": "term", "position": "preferred", "rule_text": "b", "severity": "medium"},
            {"id": "rule-1", "playbook_id": "pb-nda", "order_index": 0, "clause_type": "confidentiality", "position": "required", "rule_text": "a", "severity": "high"},
        ],
    }, auth_uid="user-1")


def test_get_playbooks_filters_by_doc_type_and_active():
    client = _client_with_playbooks()
    nda = {p["id"] for p in _run(store.get_playbooks(client, "nda"))}
    assert nda == {"pb-nda", "pb-global"}  # not lease, not inactive


def test_get_playbook_rules_orders_by_index():
    client = _client_with_playbooks()
    rules = _run(store.get_playbook_rules(client, "pb-nda"))
    assert [r["id"] for r in rules] == ["rule-1", "rule-2"]


def test_list_playbooks_with_rules_embeds_rules():
    client = _client_with_playbooks()
    playbooks = _run(store.list_playbooks_with_rules(client, "nda"))
    by_id = {p["id"]: p for p in playbooks}
    assert [r["id"] for r in by_id["pb-nda"]["rules"]] == ["rule-1", "rule-2"]
    assert by_id["pb-global"]["rules"] == []


def test_save_check_and_get_draft_attaches_last_check():
    client = _client_with_playbooks()
    draft = _run(store.create_draft(
        client, user_id="user-1", tenant_id="t1", title="D", doc_type="nda",
        skeleton=[{"heading": "Parties", "clause_type": "parties"}],
    ))
    assert _run(store.get_draft(client, draft["id"]))["last_check"] is None

    flags = _run(draft_agent.check_playbook(RULES, [{"order_index": 0, "heading": "P", "content": ""}],
                                           _FakeLLM('[{"rule_id": "r1", "status": "ok"}]')))
    saved = _run(store.save_check(
        client, draft["id"], playbook_id="pb-nda", flags=flags,
        summary=draft_agent.summarize_flags(flags), user_id="user-1",
    ))
    assert saved and saved["flags"] == flags

    latest = _run(store.get_latest_check(client, draft["id"]))
    assert latest is not None
    assert latest["playbook_id"] == "pb-nda"
    assert len(latest["flags"]) == 2  # r1 ok, r2 defaulted unclear

    fetched = _run(store.get_draft(client, draft["id"]))
    assert fetched["last_check"]["playbook_id"] == "pb-nda"

"""Session ownership logic tests (uses the RLS-simulating fake client).

Covers:
  * list_user_sessions returns only the caller's rows (RLS) with the oldest
    human message as title;
  * build_session inserts a row owned by the caller;
  * the feedback ownership query (session + user match) returns the caller's
    session only.
"""
from __future__ import annotations

import asyncio

from conftest import FakeClient

from sessions.manager import build_session, list_user_sessions


def _run(coro):
    return asyncio.run(coro)


def test_list_user_sessions_rls_scopes_to_caller(chat_tables):
    client = FakeClient(tables=chat_tables, auth_uid="user-1")
    sessions = _run(list_user_sessions(client, "user-1"))
    assert [s["session_id"] for s in sessions] == ["own-session-1"]
    assert sessions[0]["title"] == "First question about the lease."


def test_other_user_sees_only_their_rows(chat_tables):
    client = FakeClient(tables=chat_tables, auth_uid="user-2")
    sessions = _run(list_user_sessions(client, "user-2"))
    assert [s["session_id"] for s in sessions] == ["other-session-1"]


def test_build_session_creates_owned_row(chat_tables):
    client = FakeClient(tables=chat_tables, auth_uid="user-1")
    ctx = {"user_id": "user-1", "session_id": "client-side-7", "email": "u1@firm.law"}
    rbac = {"role": "associate", "perms": {"level": 2}}
    session = _run(build_session(ctx, rbac, client))
    assert session["user_id"] == "user-1"

    rows = client._tables["chat_sessions"]
    created = [r for r in rows if r["session_id"] == session["session_id"]]
    assert len(created) == 1
    assert created[0]["user_id_uuid"] == "user-1"
    assert created[0]["status"] == "active"


def test_build_session_uses_uuid4_key_not_legacy_prefix(chat_tables):
    import uuid

    client = FakeClient(tables=chat_tables, auth_uid="user-1")
    ctx = {"user_id": "user-1", "session_id": "client-side-7"}
    rbac = {"role": "associate", "perms": {"level": 2}}
    session = _run(build_session(ctx, rbac, client))
    # new sessions use a generated uuid string, never "user_id__session"
    parsed = uuid.UUID(session["session_id"])
    assert str(parsed) == session["session_id"]
    assert "user-1__" not in session["session_id"]


def test_new_session_appears_in_list_for_owner(chat_tables):
    client = FakeClient(tables=chat_tables, auth_uid="user-1")
    ctx = {"user_id": "user-1", "session_id": "client-side-8"}
    rbac = {"role": "associate", "perms": {"level": 2}}
    session = _run(build_session(ctx, rbac, client))
    sessions = _run(list_user_sessions(client, "user-1"))
    assert session["session_id"] in {s["session_id"] for s in sessions}


def test_feedback_ownership_query_returns_owner_session_only(chat_tables):
    """Mirrors the /feedback session-ownership validation in app.py."""
    owner_client = FakeClient(tables=chat_tables, auth_uid="user-1")
    res = (
        owner_client.table("chat_sessions")
        .select("session_id")
        .eq("session_id", "own-session-1")
        .eq("user_id_uuid", "user-1")
        .limit(1)
        .execute()
    )
    assert len(res.data) == 1

    # a guessed/foreign session id yields nothing for this caller
    res2 = (
        owner_client.table("chat_sessions")
        .select("session_id")
        .eq("session_id", "other-session-1")
        .eq("user_id_uuid", "user-1")
        .limit(1)
        .execute()
    )
    assert res2.data == []
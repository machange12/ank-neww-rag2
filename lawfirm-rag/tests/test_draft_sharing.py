"""Offline tests for matter-scoped draft sharing (Draft Studio).

Sharing is an explicit, READ-ONLY opt-in: the owner publishes a draft to one
matter team, and users holding a grant for that matter can read the draft (and
its sections/versions/checks) but never mutate it. These tests exercise the
store helpers, the ``can_access_matter_ref`` authorization wrapper, and the
router's owner-only write guard against the fake PostgREST client.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from conftest import FakeClient
from fastapi import HTTPException

import routers.draft as draft_router
from authz.service import can_access_matter_ref
from drafting import store


def _run(coro):
    return asyncio.run(coro)


def _make_draft(client, **kwargs):
    defaults = dict(
        user_id="user-1",
        tenant_id="t1",
        title="Shared NDA",
        doc_type="nda",
        skeleton=[{"heading": "Parties", "clause_type": "parties"}],
    )
    defaults.update(kwargs)
    return _run(store.create_draft(client, **defaults))


def _patch_auth(monkeypatch, user_id, client):
    monkeypatch.setattr(
        draft_router, "_auth", lambda token: ({"user_id": user_id, "email": "u@x"}, client)
    )


# ------------------------------------------------------------------ store

def test_visibility_defaults_private():
    client = FakeClient(auth_uid="user-1")
    draft = _make_draft(client)
    fetched = _run(store.get_draft(client, draft["id"]))
    assert fetched["visibility"] == "private"
    assert fetched["matter_id"] == ""


def test_set_draft_visibility_owner_only():
    owner = FakeClient(auth_uid="user-1")
    draft = _make_draft(owner)

    # A non-owner's UPDATE matches no row under the owner-only policy.
    intruder = FakeClient(auth_uid="user-2")
    intruder._tables = owner._tables
    assert _run(store.set_draft_visibility(
        intruder, draft["id"], visibility="matter", matter_id="M-1"
    )) is None
    assert _run(store.get_draft(owner, draft["id"]))["visibility"] == "private"

    # The owner can publish it.
    updated = _run(store.set_draft_visibility(
        owner, draft["id"], visibility="matter", matter_id="M-1"
    ))
    assert updated is not None
    assert updated["visibility"] == "matter"
    assert updated["matter_id"] == "M-1"


def test_granted_reader_sees_shared_but_not_private():
    owner = FakeClient(auth_uid="user-1")
    private = _make_draft(owner, title="Private")
    shared = _make_draft(owner, title="Shared")
    _run(store.set_draft_visibility(owner, shared["id"], visibility="matter", matter_id="M-1"))

    granted = FakeClient(auth_uid="user-2", matter_grants={"M-1"})
    granted._tables = owner._tables
    assert _run(store.get_draft(granted, shared["id"])) is not None
    assert _run(store.get_draft(granted, private["id"])) is None
    titles = {d["title"] for d in _run(store.list_drafts(granted))}
    assert titles == {"Shared"}

    # A reader without a grant for that matter sees neither.
    other = FakeClient(auth_uid="user-3", matter_grants={"M-9"})
    other._tables = owner._tables
    assert _run(store.get_draft(other, shared["id"])) is None


# ------------------------------------------------------- authorization helper

def test_can_access_matter_ref_refuses_empty_and_follows_rpc():
    client = FakeClient(
        rpcs={"auth_can_access_matter_ref": lambda params: params["p_matter_ref"] == "M-1"}
    )
    assert can_access_matter_ref(client, "M-1") is True
    assert can_access_matter_ref(client, "M-2") is False
    # '' maps to "everyone" in the SQL predicate; the wrapper must refuse it.
    assert can_access_matter_ref(client, "") is False


def test_can_access_matter_ref_fails_closed_on_error():
    def _boom(_params):
        raise RuntimeError("rpc down")

    client = FakeClient(rpcs={"auth_can_access_matter_ref": _boom})
    assert can_access_matter_ref(client, "M-1") is False


# ----------------------------------------------------------------- router

def test_share_then_unshare_round_trip(monkeypatch):
    owner = FakeClient(auth_uid="user-1")
    draft = _make_draft(owner)
    _patch_auth(monkeypatch, "user-1", owner)
    monkeypatch.setattr(draft_router, "can_access_matter_ref", lambda client, ref: ref == "M-1")

    shared = _run(draft_router.share_draft_endpoint(
        draft["id"], draft_router.ShareBody(matter_id="M-1"), None
    ))
    assert shared.status_code == 200
    body = json.loads(shared.body)
    assert body["draft"]["visibility"] == "matter"
    assert body["draft"]["matter_id"] == "M-1"

    unshared = _run(draft_router.unshare_draft_endpoint(draft["id"], None))
    assert unshared.status_code == 200
    body = json.loads(unshared.body)
    assert body["draft"]["visibility"] == "private"
    assert body["draft"]["matter_id"] == ""


def test_share_rejects_empty_matter(monkeypatch):
    owner = FakeClient(auth_uid="user-1")
    draft = _make_draft(owner)
    _patch_auth(monkeypatch, "user-1", owner)
    with pytest.raises(HTTPException) as exc:
        _run(draft_router.share_draft_endpoint(
            draft["id"], draft_router.ShareBody(matter_id="  "), None
        ))
    assert exc.value.status_code == 400


def test_share_requires_access_to_the_matter(monkeypatch):
    owner = FakeClient(auth_uid="user-1")
    draft = _make_draft(owner)
    _patch_auth(monkeypatch, "user-1", owner)
    monkeypatch.setattr(draft_router, "can_access_matter_ref", lambda client, ref: False)
    with pytest.raises(HTTPException) as exc:
        _run(draft_router.share_draft_endpoint(
            draft["id"], draft_router.ShareBody(matter_id="M-9"), None
        ))
    assert exc.value.status_code == 403


def test_non_owner_cannot_share_or_write(monkeypatch):
    owner = FakeClient(auth_uid="user-1")
    draft = _make_draft(owner)
    _run(store.set_draft_visibility(owner, draft["id"], visibility="matter", matter_id="M-1"))

    reader = FakeClient(auth_uid="user-2", matter_grants={"M-1"})
    reader._tables = owner._tables
    _patch_auth(monkeypatch, "user-2", reader)
    monkeypatch.setattr(draft_router, "can_access_matter_ref", lambda client, ref: True)

    with pytest.raises(HTTPException) as share_exc:
        _run(draft_router.share_draft_endpoint(
            draft["id"], draft_router.ShareBody(matter_id="M-1"), None
        ))
    assert share_exc.value.status_code == 403

    with pytest.raises(HTTPException) as save_exc:
        _run(draft_router.save_section_endpoint(
            draft["id"], 0, draft_router.SaveSectionBody(content="nope"), None
        ))
    assert save_exc.value.status_code == 403

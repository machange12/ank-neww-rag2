from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# The single-firm tenant id provisioned by migration 0001.
DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"


async def _fetch_tenant_id(user_client: Any, user_id: str) -> str | None:
    """Best-effort tenant id from the caller's OWN user_profiles row.

    The query runs through the caller's PostgREST client, so the
    user_profiles_select_own RLS policy returns at most the caller's row.
    """
    try:
        res = (
            user_client.table("user_profiles")
            .select("tenant_id")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        data = getattr(res, "data", None) or res
        if isinstance(data, list) and data and data[0].get("tenant_id"):
            return data[0]["tenant_id"]
    except Exception as exc:  # noqa: BLE001
        logger.warning("tenant_id lookup failed for user=%s: %s", user_id, exc)
    return None


async def build_session(
    ctx: dict[str, Any],
    rbac: dict[str, Any],
    user_client: Any,
) -> dict[str, Any]:
    """
    Create a chat session row owned by the caller and return its session dict.

    New sessions use a generated UUID string as the external session key
    (legacy keys were ``<user_id>__<client_session_id>``; ownership now always
    comes from the DB row, never from parsing the key — see migration
    20260727000003 backfill note).

    The row is inserted through the caller's own client so the
    ``chat_sessions_insert_own`` RLS policy (``user_id_uuid = auth.uid()``)
    applies. If the row cannot be written (e.g. pre-0003 schema), we degrade to
    the legacy derived key rather than breaking chat — legacy-backfill
    compatibility.
    """
    user_id = ctx["user_id"]
    session_id = str(uuid.uuid4())
    tenant_id = await _fetch_tenant_id(user_client, user_id) or DEFAULT_TENANT_ID

    row = {
        "session_id": session_id,
        "user_id": user_id,          # legacy text ownership column (not null)
        "user_id_uuid": user_id,     # authoritative ownership uuid (RLS key)
        "tenant_id": tenant_id,
        "status": "active",
        "last_activity_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        user_client.table("chat_sessions").insert(row).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "chat_sessions insert failed for user=%s; falling back to derived key: %s",
            user_id,
            exc,
        )
        session_id = f"{user_id}__{ctx['session_id']}"

    return {
        "session_id": session_id,
        "user_id": user_id,
        "role": rbac["role"],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "access_level": rbac["perms"]["level"],
        "tenant_id": tenant_id,
    }


async def list_user_sessions(user_client: Any, user_id: str, limit: int = 20) -> list:
    """Return the caller's most recent sessions with their first message as title.

    Reads go through the caller's own PostgREST client, so RLS returns only the
    caller's rows (``chat_sessions_select_own`` / ``chat_memory_select_own``).
    No service-role key is used and there is no ``<user_id>%`` ilike-prefix
    trick — ownership is enforced by the database.

    Titles come from the earliest human / HumanMessage row in chat_memory for
    each session.
    """
    resp = (
        user_client.table("chat_sessions")
        .select("session_id, created_at, last_activity_at, status")
        .order("last_activity_at", desc=True)
        .limit(max(limit * 2, 200))
        .execute()
    )
    data = getattr(resp, "data", None) or resp
    rows = data if isinstance(data, list) else []

    session_ids = [r.get("session_id") for r in rows if r.get("session_id")]
    titles: dict[str, str] = {}
    if session_ids:
        try:
            mem = (
                user_client.table("chat_memory")
                .select("session_id, message")
                .in_("session_id", session_ids)
                .order("created_at", desc=False)
                .limit(2000)
                .execute()
            )
            mem_rows = getattr(mem, "data", None) or mem
            for row in mem_rows if isinstance(mem_rows, list) else []:
                msg = row.get("message") or {}
                if msg.get("type") not in ("human", "HumanMessage"):
                    continue
                content = (msg.get("data") or {}).get("content") or msg.get("content") or ""
                sid = row.get("session_id") or ""
                # Ascending created_at → first hit per session is the oldest human msg.
                if content and sid not in titles:
                    titles[sid] = content[:60]
        except Exception as exc:  # noqa: BLE001
            logger.warning("chat_memory title lookup failed for user=%s: %s", user_id, exc)

    sessions = [
        {
            "session_id": r.get("session_id", ""),
            "title": titles.get(r.get("session_id", ""), ""),
            "created_at": r.get("last_activity_at") or r.get("created_at"),
        }
        for r in rows
    ]
    return sessions[:limit]
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from config import settings
from search.supabase_client import make_service_client


def build_session(ctx: dict[str, Any], rbac: dict[str, Any]) -> dict[str, Any]:
    """
    Mirror of n8n Session_Manager node.
    Returns a session dict; session_id is used as the Postgres chat memory key.
    """
    session_id = f"{ctx['user_id']}__{ctx['session_id']}"
    return {
        "session_id":   session_id,
        "user_id":      ctx["user_id"],
        "role":         rbac["role"],
        "started_at":   datetime.now(timezone.utc).isoformat(),
        "access_level": rbac["perms"]["level"],
    }


async def list_user_sessions(user_id: str, limit: int = 20) -> list:
    """Return the most recent sessions for a user with their first message as title."""
    # First message title = earliest human / HumanMessage row per session.
    resp = (
        make_service_client()
        .table("chat_memory")
        .select("session_id, message, created_at")
        .ilike("session_id", f"{user_id}%")
        .order("created_at", desc=True)
        .limit(2000)
        .execute()
    )
    data = getattr(resp, "data", None) or resp
    rows = data if isinstance(data, list) else []

    # Group by session, keeping the oldest human message as the title.
    # Rows are fetched newest-first, so the last human message encountered per
    # session is the oldest (the conversation starter).
    by_session: dict[str, Any] = {}
    for row in rows:
        sid = row.get("session_id") or ""
        msg = row.get("message") or {}
        msg_type = msg.get("type") or msg.get("role") or ""
        created = row.get("created_at") or ""

        if sid not in by_session:
            by_session[sid] = {"created_at": created, "first_message": None}
        if msg_type in ("human", "HumanMessage"):
            title = (msg.get("data") or {}).get("content") or msg.get("content") or ""
            # Keep overwriting: iteration is newest-to-oldest, so this yields the oldest human msg.
            by_session[sid]["first_message"] = title or by_session[sid]["first_message"]

    sessions = [
        {
            "session_id": sid,
            "title": (info["first_message"] or "")[:60],
            "created_at": info["created_at"],
        }
        for sid, info in by_session.items()
    ]
    # Sort by created_at descending for the overall most-recent-first listing.
    sessions.sort(key=lambda s: s["created_at"] or "", reverse=True)
    return sessions[:limit]

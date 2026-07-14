from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


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

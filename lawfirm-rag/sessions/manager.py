from __future__ import annotations

import datetime as dt


def build_session(ctx: dict, rbac: dict) -> dict:
    session_id = f"{ctx.get('user_id', 'anon')}__{ctx.get('session_id', '')}"
    return {
        "session_id": session_id,
        "user_id": ctx.get("user_id"),
        "role": rbac.get("role"),
        "started_at": dt.datetime.utcnow().isoformat() + "Z",
        "access_level": rbac.get("perms", {}).get("level", 1),
    }

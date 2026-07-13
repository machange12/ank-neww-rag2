from __future__ import annotations


def build_rls_filter(rbac: dict, ctx: dict, body: dict) -> dict:
    perms = rbac.get("perms") or {}
    chat_input = (
        body.get("chatInput")
        or body.get("input")
        or body.get("query")
        or body.get("message")
        or ""
    )
    return {
        "max_access_level": int(perms.get("level", 1) or 1),
        "matter_filter": None if perms.get("view_all") else list(ctx.get("matter_ids") or []),
        "chat_input": chat_input,
    }

from __future__ import annotations

from typing import Any


def build_rls_filter(
    rbac: dict[str, Any],
    ctx: dict[str, Any],
    body: dict[str, Any],
) -> dict[str, Any]:
    """
    Mirror of n8n RLS_Filter_Builder node.
    Produces the filter payload consumed by set_access_context and the RAG agent.
    matter_filter is always a list — never None — so the RPC text[] param never gets null.
    """
    perms = rbac.get("perms") or {}
    view_all: bool = perms.get("view_all", False)

    rls_filter = {
        "max_access_level": perms.get("level", 1),
        # view_all roles get [] so the RPC receives a valid empty array
        "matter_filter": [] if view_all else (ctx.get("matter_ids") or []),
        "view_all": view_all,
    }

    chat_input = (
        body.get("chatInput")
        or body.get("input")
        or body.get("query")
        or body.get("message")
        or ""
    )

    return {
        **rls_filter,
        "chat_input": chat_input,
    }

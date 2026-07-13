from __future__ import annotations

from typing import Any

from supabase import Client


async def set_access_context(client: Client, ctx: dict, rbac: dict, rls: dict) -> None:
    perms = rbac.get("perms") or {}
    try:
        await client.rpc(
            "set_access_context",
            {
                "p_access_level": rls.get("max_access_level", 1),
                "p_matter_ids": rls.get("matter_filter") or [],
                "p_view_all": bool(perms.get("view_all", False)),
                "p_user_id": ctx.get("user_id"),
                "p_role": ctx.get("role", "associate"),
            },
        ).execute()
    except Exception:
        pass

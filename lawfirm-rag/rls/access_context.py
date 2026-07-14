from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def set_access_context(
    user_client: Any,
    ctx: dict[str, Any],
    rbac: dict[str, Any],
    rls: dict[str, Any],
) -> None:
    """
    Call the Supabase set_access_context RPC using the user's own signed-in client.
    GUCs are transaction-scoped (set_config(..., true) in SQL) so they never bleed
    across pooled connections in PgBouncer transaction mode.

    Non-fatal: if the RPC fails we log and continue — the RLS policy on the
    documents table is the hard backstop.
    """
    perms = rbac.get("perms") or {}
    try:
        user_client.rpc(
            "set_access_context",
            {
                "p_access_level": rls.get("max_access_level", 1),
                "p_matter_ids":   rls.get("matter_filter") or [],
                "p_view_all":     perms.get("view_all", False),
                "p_user_id":      ctx.get("user_id"),
                "p_role":         ctx.get("role", "associate"),
            },
        ).execute()
    except Exception as exc:  # noqa: BLE001
        # Non-fatal: RLS policy on documents is the real gate
        logger.warning("set_access_context RPC warning (non-fatal): %s", exc)

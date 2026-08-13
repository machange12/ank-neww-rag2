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
<<<<<<< HEAD

    The RPC stamps the transaction-scoped GUCs (lawfirm.access_level,
    lawfirm.matter_ids, lawfirm.view_all, lawfirm.user_id, lawfirm.role) that the
    RLS policies on documents / document_metadata read. Each value is set with
    set_config(..., true) so it never bleeds across pooled connections in
    PgBouncer transaction mode.

    Matches the DB function signature:
        set_access_context(p_access_level int, p_matter_ids text[],
                           p_view_all boolean, p_user_id uuid, p_role text)
=======
    GUCs are transaction-scoped (set_config(..., true) in SQL) so they never bleed
    across pooled connections in PgBouncer transaction mode.
>>>>>>> c7d4b0571d87621b092e195d03135e276042d2fc

    Non-fatal: if the RPC fails we log and continue — the RLS policy on the
    documents table is the hard backstop.
    """
    perms = rbac.get("perms") or {}
    try:
        user_client.rpc(
            "set_access_context",
            {
                "p_access_level": rls.get("max_access_level", 1),
<<<<<<< HEAD
                "p_matter_ids": rls.get("matter_filter") or [],
                "p_view_all": perms.get("view_all", False),
                "p_user_id": ctx.get("user_id"),
                "p_role": ctx.get("role", "associate"),
=======
                "p_matter_ids":   rls.get("matter_filter") or [],
                "p_view_all":     perms.get("view_all", False),
                "p_user_id":      ctx.get("user_id"),
                "p_role":         ctx.get("role", "associate"),
>>>>>>> c7d4b0571d87621b092e195d03135e276042d2fc
            },
        ).execute()
    except Exception as exc:  # noqa: BLE001
        # Non-fatal: RLS policy on documents is the real gate
        logger.warning("set_access_context RPC warning (non-fatal): %s", exc)

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

    The RPC stamps transaction-scoped GUCs (app.user_id, app.access_level,
    app.matter_id) that the RLS policies on documents / document_metadata read.
    SET LOCAL semantics mean the values never bleed across pooled connections in
    PgBouncer transaction mode.

    NOTE: this is intentionally correct — it calls the RPC with p_user_id,
    p_access_level and the caller's first matter_id. Keep it in sync with the
    set_access_context(text, int, text) function defined in schema.sql.

    Non-fatal: if the RPC fails we log and continue — the RLS policy on the
    documents table is the hard backstop.
    """
    try:
        user_client.rpc(
            "set_access_context",
            {
                "p_user_id": ctx.get("user_id"),
                "p_access_level": int(ctx.get("access_level") or 1),
                "p_matter_id": str((ctx.get("matter_ids") or [""])[0]),
            },
        ).execute()
    except Exception as exc:  # noqa: BLE001
        # Non-fatal: RLS policy on documents is the real gate
        logger.warning("set_access_context RPC warning (non-fatal): %s", exc)

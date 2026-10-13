from __future__ import annotations

from typing import Any

from rbac.role_matrix import ROLE_MATRIX


def build_rbac_block(ctx: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    """
    Mirror of n8n RBAC_Checker_Chat node.
    Returns a dict with role permissions. (The per-session prompt prefix it
    used to build was dropped: access is enforced by RLS, and the block made
    answers inconsistent and every session's prompt unique.)
    Raises ValueError for unrecognised roles.
    """
    role = ctx.get("role", "associate")
    perms = ROLE_MATRIX.get(role)
    if not perms:
        raise ValueError(f"FORBIDDEN: Unrecognised role '{role}'")

    return {
        "role":                role,
        "perms":               perms,
    }

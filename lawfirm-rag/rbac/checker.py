from __future__ import annotations

from typing import Any

from rbac.role_matrix import ROLE_MATRIX


def build_rbac_block(ctx: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    """
    Mirror of n8n RBAC_Checker_Chat node.
    Returns a dict with role permissions and a system prompt prefix.
    Raises ValueError for unrecognised roles.
    """
    role = ctx.get("role", "associate")
    perms = ROLE_MATRIX.get(role)
    if not perms:
        raise ValueError(f"FORBIDDEN: Unrecognised role '{role}'")

    if perms["privileged"]:
        privileged_block = (
            "PRIVILEGED & CONFIDENTIAL ACCESS GRANTED: You may retrieve attorney-client "
            "privileged documents. Every response containing privileged information MUST end "
            "with: \"PRIVILEGED & CONFIDENTIAL - ATTORNEY-CLIENT PRIVILEGE PROTECTED. "
            "DO NOT FORWARD WITHOUT AUTHORIZATION.\""
        )
    else:
        privileged_block = (
            f"RESTRICTED ACCESS: Attorney-client privileged documents are NOT accessible "
            f"to your role ({role}). Do not speculate about or attempt to access privileged matters."
        )

    if perms["view_all"]:
        matter_scope = "You may access ALL matters for this firm."
    else:
        matter_scope = (
            f"You may ONLY access matters assigned to you. "
            f"Authorized matter IDs: {ctx.get('matter_ids', [])}. "
            "Refuse all queries about other matters."
        )

    system_prompt_prefix = (
        "=======================================\n"
        "LAW FIRM SECURE RAG - SESSION CONTEXT\n"
        "=======================================\n"
        f"User:         {ctx.get('email')}\n"
        f"Role:         {role.upper()}\n"
        f"Session:      {ctx.get('session_id')}\n"
        f"Access Level: {perms['level']}\n"
        "=======================================\n\n"
        f"{privileged_block}\n\n"
        f"{matter_scope}\n"
        "======================================="
    )

    return {
        "role":                role,
        "perms":               perms,
        "system_prompt_prefix": system_prompt_prefix,
    }

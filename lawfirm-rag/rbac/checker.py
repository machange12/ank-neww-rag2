from __future__ import annotations

import json

from rbac.role_matrix import get_permissions


def build_rbac_block(ctx: dict, body: dict) -> dict:
    role = ctx.get("role") or "associate"
    perms = get_permissions(role)

    privileged_block = (
        "PRIVILEGED & CONFIDENTIAL ACCESS GRANTED: You may retrieve attorney-client "
        "privileged documents. Every response containing privileged information MUST "
        "end with: \"PRIVILEGED & CONFIDENTIAL - ATTORNEY-CLIENT PRIVILEGE PROTECTED. "
        "DO NOT FORWARD WITHOUT AUTHORIZATION.\""
        if perms.privileged
        else (
            f"RESTRICTED ACCESS: Attorney-client privileged documents are NOT accessible "
            f"to your role ({role}). Do not speculate about or attempt to access privileged matters."
        )
    )

    if perms.view_all:
        matter_scope = "You may access ALL matters for this firm."
    else:
        matter_scope = (
            "You may ONLY access matters assigned to you. Authorized matter IDs: "
            f"{json.dumps(ctx.get('matter_ids') or [])}. Refuse all queries about other matters."
        )

    ip = ctx.get("ip_address", "unknown")
    session_id = ctx.get("session_id", "")
    system_prompt_prefix = (
        "=======================================\n"
        "LAW FIRM SECURE RAG - SESSION CONTEXT\n"
        "=======================================\n"
        f"User:         {ctx.get('email','unknown@ak.law')}\n"
        f"Role:         {role.upper()}\n"
        f"Session:      {session_id}\n"
        f"Access level: {perms.level}\n"
        f"IP address:   {ip}\n"
        f"View all:     {perms.view_all}\n"
        f"Privileged:   {perms.privileged}\n"
        "---------------------------------------\n"
        f"{privileged_block}\n\n"
        f"{matter_scope}\n"
        "---------------------------------------\n"
        "RESPONSE RULES:\n"
        "1. Only answer legal questions using the firm's document knowledge base.\n"
        "2. For greetings: brief reply, no tool calls.\n"
        "3. For legal questions: call the search_documents tool first.\n"
        "4. Format answers with a direct response + Sources section [Title | Date | Matter ID].\n"
        "5. If insufficient context: say so plainly.\n"
        "6. Never speculate beyond retrieved documents.\n"
        "=======================================\n"
    )

    return {
        "role": role,
        "perms": perms.to_dict(),
        "system_prompt_prefix": system_prompt_prefix,
    }

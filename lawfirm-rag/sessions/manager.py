from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import psycopg

from config import settings


def build_session(ctx: dict[str, Any], rbac: dict[str, Any]) -> dict[str, Any]:
    """
    Mirror of n8n Session_Manager node.
    Returns a session dict; session_id is used as the Postgres chat memory key.
    """
    session_id = f"{ctx['user_id']}__{ctx['session_id']}"
    return {
        "session_id":   session_id,
        "user_id":      ctx["user_id"],
        "role":         rbac["role"],
        "started_at":   datetime.now(timezone.utc).isoformat(),
        "access_level": rbac["perms"]["level"],
    }


async def list_user_sessions(user_id: str, limit: int = 20) -> list:
    """Return the most recent sessions for a user with their first message as title."""
    query = """
        WITH first_messages AS (
            SELECT DISTINCT ON (session_id)
                session_id,
                COALESCE(message->'data'->>'content', message->>'content', '') AS first_message,
                created_at
            FROM chat_memory
            WHERE session_id LIKE %s
              AND COALESCE(message->>'type', message->>'role') IN ('human', 'HumanMessage')
            ORDER BY session_id, created_at ASC
        )
        SELECT session_id, first_message, created_at
        FROM first_messages
        ORDER BY created_at DESC
        LIMIT %s
    """
    with psycopg.connect(settings.postgres_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(query, (f"{user_id}%", limit))
            rows = cur.fetchall()
    return [
        {
            "session_id": row[0],
            "title": (row[1] or "")[:60],
            "created_at": row[2].isoformat() if row[2] else "",
        }
        for row in rows
    ]

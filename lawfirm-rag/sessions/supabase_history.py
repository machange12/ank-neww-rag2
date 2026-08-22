from __future__ import annotations

from typing import Any, Sequence

from langchain_core.chat_history import BaseChatMessageHistory
from langchain_core.messages import BaseMessage, message_to_dict, messages_from_dict

from postgrest_utils import response_data


class SupabaseChatHistory(BaseChatMessageHistory):
    """
    LangChain-compatible chat history backed by the Supabase `chat_memory` table.

    Uses the caller's own Supabase REST client instead of a direct Postgres
    connection or a service-role key, so RLS applies on both reads and writes
    (``chat_memory_select_own`` / ``chat_memory_insert_own`` require
    ``user_id = auth.uid()``). Rows are stored as `message` jsonb serialized
    with langchain_core's message_to_dict, matching the format the existing
    sessions/manager.py query already reads (message->>'type',
    message->'data'->>'content').

    ``user_id`` (the JWT subject uuid) is required and is stamped onto every
    inserted row; ``tenant_id`` is optional. ``clear()`` intentionally uses a
    plain delete filtered by session_id — ownership is still enforced by RLS on
    the delete policy.
    """

    def __init__(self, session_id: str, client: Any, user_id: str, tenant_id: str | None = None) -> None:
        self.session_id = session_id
        self.client = client
        self.user_id = user_id
        self.tenant_id = tenant_id

    @property
    def messages(self) -> list[BaseMessage]:
        resp = (
            self.client.table("chat_memory")
            .select("message")
            .eq("session_id", self.session_id)
            .order("created_at", desc=False)
            .execute()
        )
        data = response_data(resp)
        rows = data if isinstance(data, list) else []
        return messages_from_dict([row.get("message") for row in rows if row.get("message")])

    def add_message(self, message: BaseMessage) -> None:
        self.add_messages([message])

    def add_messages(self, messages: Sequence[BaseMessage]) -> None:
        rows = [
            {
                "session_id": self.session_id,
                "user_id": self.user_id,
                "tenant_id": self.tenant_id,
                "message": message_to_dict(m),
            }
            for m in messages
        ]
        if rows:
            self.client.table("chat_memory").insert(rows).execute()

    def clear(self) -> None:
        self.client.table("chat_memory").delete().eq("session_id", self.session_id).execute()

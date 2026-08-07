from __future__ import annotations

from typing import Any, Sequence

from langchain_core.chat_history import BaseChatMessageHistory
from langchain_core.messages import BaseMessage, message_to_dict, messages_from_dict


class SupabaseChatHistory(BaseChatMessageHistory):
    """
    LangChain-compatible chat history backed by the Supabase `chat_memory` table.

    Uses the Supabase REST client instead of a direct Postgres connection, so it
    works on Supabase free tier (which blocks direct DB connections). Rows are
    stored as `message` jsonb serialized with langchain_core's message_to_dict,
    matching the format the existing sessions/manager.py query already reads
    (message->>'type', message->'data'->>'content').
    """

    def __init__(self, session_id: str, client: Any) -> None:
        self.session_id = session_id
        self.client = client

    @property
    def messages(self) -> list[BaseMessage]:
        resp = (
            self.client.table("chat_memory")
            .select("message")
            .eq("session_id", self.session_id)
            .order("created_at", desc=False)
            .execute()
        )
        data = getattr(resp, "data", None) or resp
        rows = data if isinstance(data, list) else []
        return messages_from_dict([row.get("message") for row in rows if row.get("message")])

    def add_message(self, message: BaseMessage) -> None:
        self.add_messages([message])

    def add_messages(self, messages: Sequence[BaseMessage]) -> None:
        rows = [
            {"session_id": self.session_id, "message": message_to_dict(m)}
            for m in messages
        ]
        if rows:
            self.client.table("chat_memory").insert(rows).execute()

    def clear(self) -> None:
        self.client.table("chat_memory").delete().eq("session_id", self.session_id).execute()

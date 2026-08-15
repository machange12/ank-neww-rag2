"""Read-only ``document_metadata`` queries used by the documents endpoints.

Previously inlined directly in app.py's ``/documents`` and
``/documents/intelligence`` handlers. Pulled out so the query shape (columns,
ordering, the explicit row-cap) lives in one place instead of being
re-written at each call site.

All reads run through the caller's own client so RLS applies — no
service-role usage here.
"""
from __future__ import annotations

from typing import Any


def list_documents(client: Any, limit: int = 100) -> list[dict[str, Any]]:
    """List indexed documents visible to the caller under RLS."""
    result = (
        client.table("document_metadata")
        .select("file_id, file_title, url, mime_type, ingested_at, access_level, matter_id")
        .order("ingested_at", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data or []


def list_document_intelligence(client: Any, access_level: int, limit: int = 1000) -> list[dict[str, Any]]:
    """doc_type + legal_entities for documents at or below the caller's access level.

    ``limit`` is explicit (rather than relying on PostgREST's implicit
    max-rows default) so a large corpus doesn't silently truncate.
    """
    res = (
        client.table("document_metadata")
        .select("file_id, file_title, doc_type, legal_entities, ingested_at")
        .lte("access_level", access_level)
        .order("ingested_at", desc=True)
        .limit(limit)
        .execute()
    )
    data = getattr(res, "data", res) or []
    return data

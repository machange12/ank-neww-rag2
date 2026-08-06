from __future__ import annotations

import logging
from typing import Any, List

from langchain_core.documents import Document
from config import settings
from providers import make_embeddings

logger = logging.getLogger(__name__)


def hybrid_search(client: Any, query_text: str, match_count: int = 20, rrf_k: int = 60) -> List[Document]:
    """
    Perform a hybrid search (vector + keyword fusion) via a Supabase RPC.
    If the RPC does not exist or fails, return an empty list so callers can
    fall back to pure vector similarity.
    """
    try:
        embedder = make_embeddings()
        # Prefer embed_query when available
        try:
            query_embedding = embedder.embed_query(query_text)
        except Exception:
            query_embedding = embedder.embed_documents([query_text])[0]

        # Call the Supabase RPC. The RPC is expected to return rows with
        # at least `content` and `metadata` columns to construct Documents.
        resp = client.rpc(
            "hybrid_search_rls",
            {
                "query_text": query_text,
                "query_embedding": query_embedding,
                "match_count": match_count,
                "rrf_k": rrf_k,
            },
        ).execute()

        data = getattr(resp, "data", None) or resp
        if not data:
            return []

        docs: List[Document] = []
        for row in data:
            content = row.get("content") or ""
            meta = row.get("metadata") or {}
            docs.append(Document(page_content=content, metadata=meta))
        return docs
    except Exception as exc:  # Graceful fallback if RPC isn't installed yet
        logger.debug("hybrid_search failed or RPC missing: %s", exc)
        return []

from __future__ import annotations

import logging
from typing import Any, List

from langchain_core.documents import Document
from config import settings
from postgrest_utils import response_data
from providers import get_embeddings

logger = logging.getLogger(__name__)


def hybrid_search(
    client: Any,
    query_text: str,
    match_count: int = 20,
    rrf_k: int = 60,
    doc_type_filter: str | None = None,
) -> List[Document]:
    """
    Perform a hybrid search (vector + keyword fusion) via a Supabase RPC.
    If the RPC does not exist or fails, return an empty list so callers can
    fall back to pure vector similarity.

    ``doc_type_filter`` is optional — when set it is forwarded to the RPC as an
    additional ``doc_type_filter`` argument, which the RPC uses to filter rows
    by ``metadata->>'doc_type'``. Passing None preserves the existing behaviour.
    """
    try:
        embedder = get_embeddings()
        # Prefer embed_query when available
        try:
            query_embedding = embedder.embed_query(query_text)
        except Exception:
            query_embedding = embedder.embed_documents([query_text])[0]

        # Call the Supabase RPC. The RPC is expected to return rows with
        # at least `content` and `metadata` columns to construct Documents.
        rpc_params: dict[str, Any] = {
            "query_text": query_text,
            "query_embedding": query_embedding,
            "match_count": match_count,
            "rrf_k": rrf_k,
        }
        if doc_type_filter:
            rpc_params["doc_type_filter"] = doc_type_filter

        resp = client.rpc("hybrid_search_rls", rpc_params).execute()

        # response_data(), not `getattr(resp, "data", None) or resp`: a
        # genuinely empty match list ([]) is falsy and `or` would silently
        # fall back to iterating the response object itself, raising
        # 'tuple' object has no attribute 'get' on every zero-match query —
        # confirmed live, previously misreported as "RPC failed" and forcing
        # every such (perfectly normal) query onto the slower vector-only
        # fallback path.
        data = response_data(resp)
        if not data:
            return []

        docs: List[Document] = []
        for row in data:
            content = row.get("content") or ""
            meta = dict(row.get("metadata") or {})
            # Row id isn't in the JSONB metadata blob itself, but callers
            # (retrieval-only eval harness) need the underlying chunk id to
            # score Recall@K against a golden set of chunk ids.
            if row.get("id") is not None:
                meta.setdefault("chunk_id", row.get("id"))
            docs.append(Document(page_content=content, metadata=meta))
        return docs
    except Exception as exc:  # Graceful fallback if RPC isn't installed yet
        # WARNING, not debug: this is the signal that retrieval silently
        # degraded to vector-only search (weaker recall, no keyword/BM25
        # signal). Previously logged at debug level with no root logging
        # configured anywhere, so this never reached any log output at all.
        logger.warning(
            "hybrid_search RPC failed or missing (falling back to vector-only "
            "similarity search) | query=%r match_count=%d doc_type_filter=%r | %s",
            query_text,
            match_count,
            doc_type_filter,
            exc,
        )
        return []

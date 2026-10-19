"""Hybrid retrieval over the curated clause bank (``draft_clauses``).

Mirrors ``search/hybrid.py`` but targets the drafting library. The RPC
(``hybrid_search_clauses``) is SECURITY INVOKER and applies the caller's
access ceiling + matter grants, so results are already authorized for the
signed-in user. Any failure returns ``[]`` so drafting degrades to
corpus-only grounding rather than erroring.
"""
from __future__ import annotations

import logging
from typing import Any, List

from langchain_core.documents import Document

from postgrest_utils import response_data
from providers import embed_prefixes

logger = logging.getLogger(__name__)

# Minimum cosine similarity for a clause to count as grounding. The RPC returns
# the top-N clauses by RRF rank regardless of relevance, so without a floor an
# unrelated section would be "grounded" on the nearest-but-irrelevant clause and
# the per-section no-source guard (agents/draft_agent.py) could never fire.
CLAUSE_MIN_SIMILARITY = 0.30


def search_clauses(
    client: Any,
    query_text: str,
    match_count: int = 8,
    rrf_k: int = 60,
    doc_type_filter: str | None = None,
    clause_type_filter: str | None = None,
) -> List[Document]:
    """Return clause-bank Documents for ``query_text`` (hybrid vector+keyword)."""
    try:
        # Local import: keeps the embedding model lazy and mirrors hybrid_search's
        # cached embedder (same query text is often embedded for both documents
        # and clauses in one section turn).
        from search.hybrid import _embed_query

        query_prefix, _ = embed_prefixes()
        query_embedding = list(_embed_query(f"{query_prefix}{query_text}"))

        rpc_params: dict[str, Any] = {
            "query_text": query_text,
            "query_embedding": query_embedding,
            "match_count": match_count,
            "rrf_k": rrf_k,
        }
        if doc_type_filter:
            rpc_params["doc_type_filter"] = doc_type_filter
        if clause_type_filter:
            rpc_params["clause_type_filter"] = clause_type_filter

        resp = client.rpc("hybrid_search_clauses", rpc_params).execute()
        data = response_data(resp)
        if not data:
            return []

        docs: List[Document] = []
        for row in data:
            body = row.get("body") or ""
            if not body:
                continue
            similarity = row.get("similarity")
            if similarity is None or float(similarity) < CLAUSE_MIN_SIMILARITY:
                continue
            title = row.get("title") or "Clause"
            meta = {
                "kind": "clause",
                "file_id": f"clause:{row.get('id')}",
                "file_title": title,
                "clause_type": row.get("clause_type") or "",
                "doc_type": row.get("doc_type") or "",
                "chunk_index": 0,
                "matter_id": row.get("matter_id") or "",
            }
            docs.append(Document(page_content=body, metadata=meta))
        return docs
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "clause hybrid search failed (drafting falls back to corpus-only) "
            "| query=%r | %s",
            query_text,
            exc,
        )
        return []

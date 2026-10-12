"""Local cross-encoder reranker. Drop-in replacement for CohereRerank.

BAAI/bge-reranker-v2-m3 is small (~560MB), multilingual, and typically
matches or beats Cohere rerank-english-v3.0 on legal/QA sets — with no
API key, no network hop, no per-call cost.

ponytail: singleton, in-process, ~500MB resident. Restart to swap models.
"""
from __future__ import annotations

import logging
from typing import Any, Sequence

from langchain_core.callbacks import Callbacks
from langchain_core.documents import BaseDocumentCompressor, Document
from pydantic import ConfigDict, Field

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "BAAI/bge-reranker-v2-m3"

_cross_encoder: Any = None


def get_cross_encoder(model_name: str = DEFAULT_MODEL) -> Any:
    """Lazy singleton — first call downloads/loads the model (~30s cold)."""
    global _cross_encoder
    if _cross_encoder is not None:
        return _cross_encoder
    from sentence_transformers import CrossEncoder

    _cross_encoder = CrossEncoder(model_name, max_length=512)
    return _cross_encoder


class LocalCrossEncoderReranker(BaseDocumentCompressor):
    """LangChain-compatible reranker backed by a local sentence-transformers CrossEncoder."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    top_n: int = 5
    # Hard cap on how many candidates the cross-encoder scores. MultiQueryRetriever
    # + k=20 can deliver 60-80 unique docs; CPU CE takes ~50ms/pair, so scoring
    # 80 pairs is a 4s tax on every query. Trim upstream candidates to this many
    # (by the fusion order they came in — hybrid RRF has already ranked them).
    # ponytail: naive slice, no re-scoring. If quality drops on long-tail queries,
    # raise this or move CE to GPU.
    max_candidates: int = 25
    model_name: str = Field(default=DEFAULT_MODEL)

    def compress_documents(
        self,
        documents: Sequence[Document],
        query: str,
        callbacks: Callbacks | None = None,
    ) -> Sequence[Document]:
        if not documents:
            return []
        candidates = list(documents)[: self.max_candidates]
        model = get_cross_encoder(self.model_name)
        pairs = [(query, d.page_content) for d in candidates]
        scores = model.predict(pairs, show_progress_bar=False)
        # Attach score to metadata so upstream logging/eval can see it,
        # then take top_n by score descending.
        ranked = sorted(zip(scores, candidates), key=lambda x: float(x[0]), reverse=True)
        out: list[Document] = []
        for score, doc in ranked[: self.top_n]:
            meta = dict(doc.metadata or {})
            meta["rerank_score"] = float(score)
            out.append(Document(page_content=doc.page_content, metadata=meta))
        return out


if __name__ == "__main__":  # smoke test
    r = LocalCrossEncoderReranker(top_n=2)
    docs = [
        Document(page_content="Bananas are yellow fruit."),
        Document(page_content="The Kenyan Constitution establishes the judiciary in Chapter 10."),
        Document(page_content="Weather forecast for Nairobi tomorrow."),
    ]
    out = r.compress_documents(docs, "How is the judiciary set up in Kenya?")
    assert len(out) == 2
    assert "judiciary" in out[0].page_content.lower(), out
    print("ok", [d.metadata["rerank_score"] for d in out])

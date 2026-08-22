"""
experiment_chunk_sizes.py
==========================
Compares chunk-size/strategy variants (500 / 900-baseline / 1000 tokens, plus
an optional semantic-chunking variant) against the Phase 1 retrieval golden
set, measuring Recall@K/MRR for each so a chunk-size decision is backed by
data instead of a guess.

Design note: this experiment runs entirely IN MEMORY and never writes to the
Supabase ``documents`` table. Each variant re-chunks + re-embeds the same
source documents, then ranks chunks per query by cosine similarity in
Python (numpy) rather than going through the ``hybrid_search_rls`` RPC. This
sidesteps two problems with an insert-then-query-then-delete approach: (1)
the RPC's RLS/matter_id filtering machinery would need to be worked around
for isolated per-variant querying, and (2) any partial failure would risk
leaving experiment rows mixed into production search results. Because the
RPC's keyword/BM25 branch doesn't depend on chunk boundaries in a way this
particular experiment needs to isolate, comparing pure embedding-recall
across chunk-size variants is a faithful (and much lower-risk) proxy for the
question being asked here: "does chunk size X put the right passage in the
embedding model's top-K more often than chunk size Y?"

Ground truth uses "relevant_text_snippet" (or falls back to "relevant_file_ids")
from the golden set, NOT "relevant_chunk_ids" — re-chunking changes chunk
boundaries, so ids from the golden set (which reference production chunk
rows) don't apply to freshly re-chunked variants.

Source text for each file_id is reconstructed by concatenating that file's
EXISTING production chunks (ordered by chunk_index) rather than re-extracting
from Drive — a reasonable approximation for this comparison (some duplicated
text at chunk-overlap boundaries), and avoids re-implementing Drive download
+ text extraction here.

Usage:
    python scripts/experiment_chunk_sizes.py golden_sets/retrieval_golden_set.json
    python scripts/experiment_chunk_sizes.py golden_sets/retrieval_golden_set.json --semantic
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingest.embed import TextChunk, chunk_text, embed_chunks
from postgrest_utils import response_data
from providers import get_embeddings
from search.service_client import make_service_client

K_VALUES = (5, 10, 20)
MAX_K = max(K_VALUES)

VARIANTS: list[tuple[str, int, int]] = [
    ("500tok", 500, 50),
    ("900tok_baseline", 900, 80),
    ("1000tok", 1000, 90),
]


def _reconstruct_source_text(client: Any, file_id: str) -> tuple[str, str]:
    """Concatenate a file's existing production chunks (by chunk_index) to
    approximate its original extracted text. Returns (text, file_title)."""
    resp = (
        client.table("documents")
        .select("content, metadata")
        .filter("metadata->>file_id", "eq", file_id)
        .execute()
    )
    rows = response_data(resp) or []
    if not rows:
        return "", ""
    rows.sort(key=lambda r: (r.get("metadata") or {}).get("chunk_index", 0) or 0)
    file_title = (rows[0].get("metadata") or {}).get("file_title", "")
    text = "\n\n".join(r.get("content") or "" for r in rows)
    return text, file_title


def _semantic_chunks(text: str) -> list[TextChunk] | None:
    try:
        from langchain_experimental.text_splitter import SemanticChunker
    except ImportError:
        print(
            "langchain-experimental not installed — skipping semantic variant "
            "(pip install langchain-experimental to enable).",
            file=sys.stderr,
        )
        return None
    splitter = SemanticChunker(get_embeddings())
    raw_chunks = splitter.split_text(text)
    return [TextChunk(text=c, section_heading="", chunk_index=i) for i, c in enumerate(raw_chunks)]


def _cosine(a: list[float], b: list[float]) -> float:
    import numpy as np

    va, vb = np.array(a), np.array(b)
    denom = (np.linalg.norm(va) * np.linalg.norm(vb)) or 1e-9
    return float(np.dot(va, vb) / denom)


def _build_variant_pool(
    client: Any, file_ids: list[str], chunk_size: int | None, chunk_overlap: int | None, semantic: bool
) -> list[dict[str, Any]]:
    """Chunk + embed every golden-set source file for one variant. Returns a
    flat pool of {content, file_id, embedding} dicts across all files."""
    pool: list[dict[str, Any]] = []
    for file_id in file_ids:
        text, file_title = _reconstruct_source_text(client, file_id)
        if not text:
            print(f"  (skipping file_id={file_id!r}: no existing chunks found to reconstruct source text)", file=sys.stderr)
            continue
        if semantic:
            chunks = _semantic_chunks(text)
            if chunks is None:
                continue
        else:
            chunks = chunk_text(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        embeddings = embed_chunks(chunks, file_title=file_title)
        for chunk, embedding in zip(chunks, embeddings):
            pool.append({"content": chunk.text, "file_id": file_id, "embedding": embedding})
    return pool


def _score_variant(pool: list[dict[str, Any]], cases: list[dict[str, Any]]) -> dict[str, Any]:
    embedder = get_embeddings()
    recall_totals = {k: 0.0 for k in K_VALUES}
    mrr_total = 0.0
    scored = 0

    for case in cases:
        query = case["query"]
        snippet = (case.get("relevant_text_snippet") or "").strip().lower()
        relevant_files = set(case.get("relevant_file_ids") or [])
        if not snippet and not relevant_files:
            continue  # nothing in this variant-agnostic pool to check against

        try:
            q_embedding = embedder.embed_query(query)
        except Exception:
            q_embedding = embedder.embed_documents([query])[0]

        ranked = sorted(pool, key=lambda row: _cosine(q_embedding, row["embedding"]), reverse=True)

        def is_hit(row: dict[str, Any]) -> bool:
            if snippet and snippet in row["content"].lower():
                return True
            if relevant_files and row["file_id"] in relevant_files:
                return True
            return False

        for k in K_VALUES:
            recall_totals[k] += 1.0 if any(is_hit(r) for r in ranked[:k]) else 0.0

        for rank, row in enumerate(ranked, start=1):
            if is_hit(row):
                mrr_total += 1.0 / rank
                break

        scored += 1

    if scored == 0:
        return {"recall_at_k": {k: 0.0 for k in K_VALUES}, "mrr": 0.0, "n": 0}

    return {
        "recall_at_k": {k: recall_totals[k] / scored for k in K_VALUES},
        "mrr": mrr_total / scored,
        "n": scored,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("golden_set", type=Path)
    parser.add_argument("--semantic", action="store_true", help="also run a SemanticChunker variant")
    args = parser.parse_args()

    cases = json.loads(args.golden_set.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases:
        print("Golden set must be a non-empty JSON array.", file=sys.stderr)
        sys.exit(1)

    file_ids = sorted({fid for case in cases for fid in (case.get("relevant_file_ids") or [])})
    if not file_ids:
        print("Golden set has no relevant_file_ids — nothing to re-chunk.", file=sys.stderr)
        sys.exit(1)

    client = make_service_client()

    variants = list(VARIANTS)
    print(f"Reconstructing + re-chunking {len(file_ids)} source file(s) for {len(variants)} size variant(s)...\n")

    results: dict[str, dict[str, Any]] = {}
    for label, chunk_size, chunk_overlap in variants:
        print(f"Variant: {label} (chunk_size={chunk_size}, chunk_overlap={chunk_overlap})")
        pool = _build_variant_pool(client, file_ids, chunk_size, chunk_overlap, semantic=False)
        results[label] = _score_variant(pool, cases)

    if args.semantic:
        print("Variant: semantic (SemanticChunker)")
        pool = _build_variant_pool(client, file_ids, None, None, semantic=True)
        if pool:
            results["semantic"] = _score_variant(pool, cases)

    header = f"{'Variant':<20}" + "".join(f" R@{k:<3}" for k in K_VALUES) + f" {'MRR':<6} n"
    print(f"\n{header}")
    print("-" * len(header))
    for label, r in results.items():
        recall_cols = "".join(f" {r['recall_at_k'][k]:<4.0%}" for k in K_VALUES)
        print(f"{label:<20}{recall_cols} {r['mrr']:<6.2f} {r['n']}")

    print(
        "\nDecision rule: pick the smallest chunk size that doesn't regress "
        "Recall@10 by more than ~5% (relative) vs. the best-performing size. "
        "Prefer semantic chunking only if it beats the best fixed size by a "
        "clear margin, not noise-level, given this sample size."
    )
    print("\nNote: this script makes no writes to Supabase — nothing to clean up.")


if __name__ == "__main__":
    main()

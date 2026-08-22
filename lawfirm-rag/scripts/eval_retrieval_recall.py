"""
eval_retrieval_recall.py
=========================
Retrieval-only eval harness: measures whether the retriever actually finds
the right chunks, independent of whether the LLM goes on to cite them.

Unlike eval_retrieval.py (black-box, hits the running HTTP backend, scores
the final LLM answer), this script imports the retrieval code directly and
scores ranked chunk ids against a hand-labeled golden set. It connects
straight to Supabase via the service-role client (search/service_client.py)
and computes embeddings via providers.get_embeddings() — no FastAPI process,
no login flow, no LLM answer generation involved for the "raw" mode.

Two modes, selectable per run:
  raw   - calls search.hybrid.hybrid_search() directly. Cleanest signal:
          just the RRF-fused vector+keyword search, no query rewriting or
          reranking on top.
  full  - runs agents.rag_agent._make_retriever()'s full chain (hybrid +
          MultiQueryRetriever query expansion + optional Cohere/local
          rerank), i.e. what a real chat turn actually retrieves with.

Comparing raw vs full on the same golden set isolates how much each stage
(query expansion, reranking) is helping or hurting recall.

Golden-set file format (JSON array):
    [
      {
        "query": "What are the notice requirements for a data breach?",
        "relevant_chunk_ids": [123, 456],
        "relevant_file_ids": ["upload_...-dpa-guidance.pdf"],
        "doc_type": "statute"
      },
      ...
    ]

"relevant_chunk_ids" (documents.id values) is the primary ground truth for
Recall@K/MRR. "relevant_file_ids" is used as a coarser fallback signal (any
chunk from that file counts as a hit) when exact chunk ids aren't labeled
yet. "doc_type" is optional, used only for the per-doc_type breakdown.

Optional "relevant_text_snippet": a short substring expected to appear in a
hit chunk's content. Re-chunking (different chunk_size/chunk_overlap, or a
different splitter entirely) changes chunk boundaries and ids, so exact
chunk-id truth doesn't transfer across chunking variants — this field lets
scripts/experiment_chunk_sizes.py score recall against re-chunked variants
by substring match instead of id match.

See scripts/dump_chunks_for_labeling.py to find chunk ids for a given file
quickly when building this golden set by hand.

Usage:
    python scripts/eval_retrieval_recall.py golden_sets/retrieval_golden_set.json
    python scripts/eval_retrieval_recall.py golden_sets/retrieval_golden_set.json --mode full
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from search.hybrid import hybrid_search
from search.service_client import make_service_client

K_VALUES = (5, 10, 20)
MAX_K = max(K_VALUES)


def _relevant_ids(case: dict[str, Any]) -> set[Any]:
    return {cid for cid in (case.get("relevant_chunk_ids") or [])}


def _relevant_file_ids(case: dict[str, Any]) -> set[str]:
    return {fid for fid in (case.get("relevant_file_ids") or [])}


def _retrieved_ids_and_files(doc: Any) -> tuple[Any, str, str]:
    meta = getattr(doc, "metadata", {}) or {}
    return meta.get("chunk_id"), meta.get("file_id", ""), getattr(doc, "page_content", "") or ""


def _run_case_raw(client: Any, case: dict[str, Any]) -> dict[str, Any]:
    query = case["query"]
    started = time.monotonic()
    docs = hybrid_search(client, query, match_count=MAX_K)
    latency = time.monotonic() - started
    return _score_case(case, docs, latency)


def _run_case_full(client: Any, llm: Any, case: dict[str, Any]) -> dict[str, Any]:
    from agents.rag_agent import _make_retriever

    query = case["query"]
    doc_type_filter = case.get("doc_type")
    retriever, _ = _make_retriever(client, llm, k=MAX_K, doc_type_filter=doc_type_filter)
    started = time.monotonic()
    docs = retriever.invoke(query)
    latency = time.monotonic() - started
    return _score_case(case, docs, latency)


def _score_case(case: dict[str, Any], docs: list[Any], latency: float) -> dict[str, Any]:
    query = case["query"]
    relevant_ids = _relevant_ids(case)
    relevant_files = _relevant_file_ids(case)
    snippet = (case.get("relevant_text_snippet") or "").strip().lower()

    ranked = [_retrieved_ids_and_files(d) for d in docs]

    def is_hit(idx: int) -> bool:
        chunk_id, file_id, content = ranked[idx]
        if relevant_ids and chunk_id in relevant_ids:
            return True
        if relevant_files and file_id in relevant_files:
            return True
        if snippet and snippet in content.lower():
            return True
        return False

    recall_at_k = {}
    for k in K_VALUES:
        window = range(min(k, len(ranked)))
        recall_at_k[k] = 1.0 if any(is_hit(i) for i in window) else 0.0

    rr = 0.0
    for rank, _ in enumerate(ranked, start=1):
        if is_hit(rank - 1):
            rr = 1.0 / rank
            break

    return {
        "query": query,
        "doc_type": case.get("doc_type", ""),
        "recall_at_k": recall_at_k,
        "reciprocal_rank": rr,
        "docs_retrieved": len(docs),
        "latency": latency,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("golden_set", type=Path)
    parser.add_argument(
        "--mode",
        choices=("raw", "full"),
        default="raw",
        help="raw = hybrid_search() only; full = the whole retriever chain (multi-query + rerank).",
    )
    args = parser.parse_args()

    cases = json.loads(args.golden_set.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases:
        print("Golden set must be a non-empty JSON array.", file=sys.stderr)
        sys.exit(1)

    client = make_service_client()

    if args.mode == "full":
        from providers import make_chat_llm

        llm = make_chat_llm()
        results = [_run_case_full(client, llm, case) for case in cases]
    else:
        results = [_run_case_raw(client, case) for case in cases]

    header = f"{'Query':<50} {'DocType':<10}" + "".join(f" R@{k:<3}" for k in K_VALUES) + f" {'MRR':<6} Latency"
    print(f"\nMode: {args.mode}")
    print(header)
    print("-" * len(header))
    for r in results:
        recall_cols = "".join(f" {r['recall_at_k'][k]:<4.0%}" for k in K_VALUES)
        print(
            f"{r['query'][:48]:<50} {r['doc_type'][:10]:<10}{recall_cols} "
            f"{r['reciprocal_rank']:<6.2f} {r['latency']:.2f}s"
        )

    print("-" * len(header))
    n = len(results)
    for k in K_VALUES:
        mean_recall = sum(r["recall_at_k"][k] for r in results) / n
        print(f"Mean Recall@{k}: {mean_recall:.0%}")
    mean_mrr = sum(r["reciprocal_rank"] for r in results) / n
    print(f"Mean MRR: {mean_mrr:.3f}")

    by_type: dict[str, list[dict[str, Any]]] = {}
    for r in results:
        by_type.setdefault(r["doc_type"] or "(unspecified)", []).append(r)
    if len(by_type) > 1:
        print("\nBy doc_type:")
        for doc_type, group in sorted(by_type.items()):
            n_group = len(group)
            recalls = ", ".join(
                f"R@{k}={sum(r['recall_at_k'][k] for r in group) / n_group:.0%}" for k in K_VALUES
            )
            print(f"  {doc_type:<12} n={n_group:<3} {recalls}")


if __name__ == "__main__":
    main()

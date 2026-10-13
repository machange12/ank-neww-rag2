"""
eval_golden.py
==============
Accuracy check against a golden set whose answers are known exactly
(scripts/golden_sets/synthetic_golden.json, built from the fictional
documents in scripts/golden_sets/synthetic_docs/).

Runs as a real user (RLS applies), so every number reflects what that user
would actually get:

  retrieval   for answerable questions: is the right document in the top
              1/3/5 (hit@k), how high (MRR), and does a returned chunk
              contain the exact expected passage (passage@5)?
              - "hybrid": the raw hybrid search
              - "pipeline": the 5 chunks the answer model actually sees
                (multi-query + rerank), i.e. what a chat turn uses
  behaviour   negative questions must reply "could not find";
              ambiguous questions must ask which document is meant.

Usage (documents must already be ingested with file_id "synthetic_<name>"):
    EVAL_EMAIL=partner@ak.law EVAL_PASSWORD=... python scripts/eval_golden.py
    ... python scripts/eval_golden.py --no-behaviour   # retrieval only, no LLM calls
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import json
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from supabase import create_client  # noqa: E402

import agents.rag_agent as ra  # noqa: E402
from config import settings  # noqa: E402
from deps import make_user_client  # noqa: E402
from providers import make_chat_llm  # noqa: E402
from search.hybrid import hybrid_search  # noqa: E402

GOLDEN = Path(__file__).parent / "golden_sets" / "synthetic_golden.json"


def score(docs, case) -> dict:
    want = f"synthetic_{case['file']}"
    files = [(d.metadata or {}).get("file_id") for d in docs]
    rank = next((i + 1 for i, f in enumerate(files) if f == want), None)
    passage = any(case["snippet"] in d.page_content for d in docs[:5])
    return {"hit@1": rank == 1, "hit@3": bool(rank and rank <= 3), "hit@5": bool(rank and rank <= 5),
            "passage@5": passage, "mrr": 1 / rank if rank else 0.0}


def summarise(name: str, rows: list[dict]) -> None:
    n = len(rows)
    cols = "  ".join(f"{k}={sum(r[k] for r in rows) / n:6.1%}" for k in ("hit@1", "hit@3", "hit@5", "passage@5"))
    print(f"{name:28s} n={n:<3} {cols}  MRR={sum(r['mrr'] for r in rows) / n:.3f}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-behaviour", action="store_true")
    args = ap.parse_args()

    cases = json.loads(GOLDEN.read_text(encoding="utf-8"))
    anon = create_client(settings.supabase_url, settings.supabase_anon_key)
    session = anon.auth.sign_in_with_password({"email": os.environ["EVAL_EMAIL"], "password": os.environ["EVAL_PASSWORD"]}).session
    uc, user_id = make_user_client(session.access_token), session.user.id
    llm = make_chat_llm(False)

    answerable = [c for c in cases if c["file"]]
    results: dict[str, list[dict]] = {"hybrid": [], "pipeline": []}
    misses = []
    for c in answerable:
        results["hybrid"].append(score(hybrid_search(uc, c["query"], match_count=10), c))
        intent = ra.classify_intent(c["query"])
        retriever, _ = ra._make_retriever(uc, llm, k=intent["retrieve_k"],
                                         use_multi_query=intent["intent"] in ra.MULTI_QUERY_INTENTS)
        s = score(await retriever.ainvoke(c["query"]), c)
        results["pipeline"].append(s)
        if not s["passage@5"]:
            misses.append((c["type"], "doc ok" if s["hit@5"] else "WRONG DOC", c["query"]))

    print(f"\nRetrieval ({len(answerable)} answerable questions)")
    for name, rows in results.items():
        summarise(name, rows)
    print("\nPipeline by question type")
    by_type = collections.defaultdict(list)
    for c, s in zip(answerable, results["pipeline"]):
        by_type[c["type"]].append(s)
    for t, rows in sorted(by_type.items()):
        summarise(f"  {t}", rows)
    if misses:
        print("\nPipeline misses (expected passage not in the 5 chunks the model sees):")
        for t, why, q in misses:
            print(f"  [{t}] {why}: {q}")

    if args.no_behaviour:
        return
    print("\nBehaviour")
    ra.clear_answer_cache()
    for c in (c for c in cases if c["type"] in ("negative", "ambiguous")):
        r = await ra.run_chat(str(uuid.uuid4()), c["query"], uc, user_id)
        ans = r["answer"]
        ok = ans.startswith("I could not find") if c["type"] == "negative" else ans.startswith("Your question could refer")
        print(f"  {'PASS' if ok else 'FAIL'} [{c['type']}] {c['query']}\n        -> {ans[:140]!r}")


if __name__ == "__main__":
    asyncio.run(main())

"""
eval_retrieval.py
==================
Offline-friendly retrieval-quality eval harness: runs a golden set of
(query, expected result) pairs against a running backend and reports
hit-rate / zero-result-rate. Closes the "no way to measure retrieval quality"
gap — previously the only signal was reading logs by eye, and even that was
broken (see logging_setup.configure_logging).

This is a black-box check against the final chat response (what a user
actually sees), not an internal retriever probe — it exercises the whole
pipeline (intent routing, hybrid/vector retrieval, rerank, hallucination
guard) exactly as a real query would.

Golden-set file format (JSON array):
    [
      {
        "query": "What are the notice requirements for a data breach?",
        "expected_file_ids": ["upload_...-dpa-guidance.pdf"],
        "expected_keywords": ["72 hours", "notification"]
      },
      {
        "query": "What is the capital of France?",
        "expect_zero_result": true
      },
      ...
    ]

"expected_file_ids" and "expected_keywords" are optional; a case with neither
(and without "expect_zero_result") only measures whether retrieval returned
*something*. Set "expect_zero_result": true for negative cases that should
correctly trigger the hallucination guard / out-of-scope response — without
it, a query with no expectations that happens to return nothing is scored as
a failure, not a correctly-working guard. See eval_retrieval.example.json for
a template — copy it, then fill in real file_ids from a `GET /documents` call
against your own indexed corpus.

Usage:
    python scripts/eval_retrieval.py <email> <password> <golden_set.json> \
        [--base-url http://localhost:8001]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

# Must stay in sync with agents/rag_agent.py's canned responses — this script
# is a black-box HTTP client, so it can't import them directly against a
# remote backend.
NO_RESULT_RESPONSE = "I could not find an answer in the firm's document repository."
OUT_OF_SCOPE_RESPONSE = "This question is outside the scope of the firm's document repository."


def _login(base_url: str, email: str, password: str) -> str:
    resp = httpx.post(f"{base_url}/auth/login", json={"email": email, "password": password}, timeout=30)
    resp.raise_for_status()
    return resp.json()["access_token"]


def _run_case(base_url: str, token: str, case: dict[str, Any]) -> dict[str, Any]:
    query = case["query"]
    expected_file_ids = set(case.get("expected_file_ids") or [])
    expected_keywords = [kw.lower() for kw in (case.get("expected_keywords") or [])]
    expect_zero_result = bool(case.get("expect_zero_result", False))

    started = time.monotonic()
    resp = httpx.post(
        f"{base_url}/lawfirm-chat-trigger-006",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"chatInput": query},
        timeout=60,
    )
    latency = time.monotonic() - started

    if resp.status_code != 200:
        return {"query": query, "error": f"HTTP {resp.status_code}: {resp.text[:200]}", "latency": latency}

    data = resp.json()
    answer = data.get("output") or data.get("answer") or ""
    sources = data.get("sources") or []
    returned_file_ids = {s.get("file_id") for s in sources if s.get("file_id")}

    zero_result = answer.strip() in (NO_RESULT_RESPONSE, OUT_OF_SCOPE_RESPONSE)

    if expect_zero_result:
        # Negative case: passes exactly when the hallucination guard fired.
        passed = zero_result
        file_id_hit = keyword_hit = None
    else:
        file_id_hit = bool(expected_file_ids & returned_file_ids) if expected_file_ids else None
        keyword_hit = (
            any(kw in answer.lower() for kw in expected_keywords) if expected_keywords else None
        )
        # A case "passes" if every check that was actually specified passed,
        # and retrieval didn't come back empty.
        checks = [c for c in (file_id_hit, keyword_hit) if c is not None]
        passed = (not zero_result) and (all(checks) if checks else True)

    return {
        "query": query,
        "expect_zero_result": expect_zero_result,
        "zero_result": zero_result,
        "file_id_hit": file_id_hit,
        "keyword_hit": keyword_hit,
        "sources_returned": len(sources),
        "passed": passed,
        "latency": latency,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("email")
    parser.add_argument("password")
    parser.add_argument("golden_set", type=Path)
    parser.add_argument("--base-url", default="http://localhost:8001")
    args = parser.parse_args()

    cases = json.loads(args.golden_set.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases:
        print("Golden set must be a non-empty JSON array.", file=sys.stderr)
        sys.exit(1)

    token = _login(args.base_url, args.email, args.password)

    results = [_run_case(args.base_url, token, case) for case in cases]

    print(f"\n{'Query':<55} {'Result':<10} {'FileID':<8} {'Keyword':<8} {'Sources':<8} Latency")
    print("-" * 100)
    for r in results:
        if "error" in r:
            print(f"{r['query'][:53]:<55} ERROR: {r['error']}")
            continue
        result = "PASS" if r["passed"] else ("ZERO" if r["zero_result"] else "FAIL")
        file_id_col = "-" if r["file_id_hit"] is None else ("hit" if r["file_id_hit"] else "MISS")
        keyword_col = "-" if r["keyword_hit"] is None else ("hit" if r["keyword_hit"] else "MISS")
        print(
            f"{r['query'][:53]:<55} {result:<10} {file_id_col:<8} {keyword_col:<8} "
            f"{r['sources_returned']:<8} {r['latency']:.1f}s"
        )

    scored = [r for r in results if "error" not in r]
    total = len(scored)
    passed = sum(1 for r in scored if r["passed"])
    zero_result = sum(1 for r in scored if r["zero_result"])
    errors = len(results) - total

    print("-" * 100)
    print(f"Pass rate: {passed}/{total} ({passed / total * 100:.0f}%)" if total else "Pass rate: n/a")
    print(f"Zero-result rate: {zero_result}/{total} ({zero_result / total * 100:.0f}%)" if total else "")
    if errors:
        print(f"Request errors: {errors}")


if __name__ == "__main__":
    main()

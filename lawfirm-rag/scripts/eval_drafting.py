"""
eval_drafting.py
================
Groundedness check for the Draft Studio against a golden set whose per-section
expectations are known (scripts/golden_sets/drafting_golden.json).

It runs the real section generator (agents/draft_agent.generate_section) as a
signed-in user, so RLS + the no-source guard apply exactly as in production,
and reports three things the drafting safety model cares about:

  groundedness          sections that should be supported ARE generated and
                        cite a source that is actually returned
  citation validity     no generated section cites a source index it was not
                        given (the model must never invent [n])
  needs_source          sections with NO adequate grounding are flagged
                        ``needs_source`` and never filled with invented text

A section is scored against its ``expect`` field:
  "grounded"      -> status "generated", valid citations, and (when
                     ``expected_file_ids`` is given) every listed file_id in
                     the returned sources
  "needs_source"  -> status "needs_source" and no sources attached

The scorers (``citation_validity``, ``score_section``, ``summarize_runs``) are
pure and unit-tested offline (tests/test_drafting_eval.py). Running the harness
itself needs a live, RLS-scoped Supabase project with documents ingested and the
curated clause/playbook library seeded, plus credentials in the environment,
exactly like scripts/eval_golden.py.

Usage:
    EVAL_EMAIL=partner@ak.law EVAL_PASSWORD=... python scripts/eval_drafting.py
    ... python scripts/eval_drafting.py --file scripts/golden_sets/drafting_golden.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

GOLDEN = Path(__file__).parent / "golden_sets" / "drafting_golden.json"

# Citations are emitted as bracketed integers ("[1]", "[2, 3]"); pull every
# integer out of the brackets so an out-of-range or invented index is caught.
CITATION_RE = re.compile(r"\[([\d,\s]+)\]")


def cited_numbers(text: str) -> list[int]:
    """Return every integer citation index used in ``text`` (in order)."""
    numbers: list[int] = []
    for group in CITATION_RE.findall(text or ""):
        for part in group.split(","):
            part = part.strip()
            if part.isdigit():
                numbers.append(int(part))
    return numbers


def citation_validity(content: str, n_sources: int) -> dict:
    """Check every citation in ``content`` points at one of ``n_sources``."""
    numbers = cited_numbers(content)
    invalid = [n for n in numbers if n < 1 or n > n_sources]
    return {"cited": len(numbers), "invalid": invalid, "valid": not invalid}


def score_section(result: dict, expectation: dict) -> dict:
    """Score one generated section against its golden expectation.

    Returns a row with a boolean ``pass`` plus the sub-signals used to reach it,
    so failures can be diagnosed without re-running the model.
    """
    expect = expectation.get("expect", "grounded")
    status = result.get("status")
    content = result.get("content") or ""
    sources = result.get("sources") or []
    validity = citation_validity(content, len(sources))

    row = {
        "expect": expect,
        "status": status,
        "valid_citations": validity["valid"],
        "cited": validity["cited"],
        "n_sources": len(sources),
        "files_ok": True,
    }
    if expect == "needs_source":
        # The model must not have been handed anything to fill the gap.
        row["pass"] = status == "needs_source" and not sources
    else:
        want = set(expectation.get("expected_file_ids") or [])
        got = {s.get("file_id") or "" for s in sources}
        row["files_ok"] = want.issubset(got) if want else True
        row["pass"] = (
            status == "generated"
            and bool(content.strip())
            and validity["valid"]
            and row["files_ok"]
        )
    return row


def summarize_runs(rows: list[dict]) -> dict:
    """Aggregate scored rows into groundedness / citation / guard rates."""
    n = len(rows)
    if not n:
        return {"sections": 0}

    grounded = [r for r in rows if r["expect"] != "needs_source"]
    unsupported = [r for r in rows if r["expect"] == "needs_source"]

    def rate(subset: list[dict]) -> float | None:
        return sum(1 for r in subset if r["pass"]) / len(subset) if subset else None

    return {
        "sections": n,
        "pass_rate": rate(rows),
        "groundedness": rate(grounded),
        "grounded_cases": len(grounded),
        "needs_source_correct": rate(unsupported),
        "needs_source_cases": len(unsupported),
        "citation_validity": sum(1 for r in rows if r["valid_citations"]) / n,
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(GOLDEN))
    args = ap.parse_args()

    from supabase import create_client  # noqa: E402

    from agents.draft_agent import format_brief, generate_section  # noqa: E402
    from config import settings  # noqa: E402
    from deps import make_user_client  # noqa: E402
    from providers import make_chat_llm  # noqa: E402

    cases = json.loads(Path(args.file).read_text(encoding="utf-8"))
    anon = create_client(settings.supabase_url, settings.supabase_anon_key)
    session = anon.auth.sign_in_with_password(
        {"email": os.environ["EVAL_EMAIL"], "password": os.environ["EVAL_PASSWORD"]}
    ).session
    uc = make_user_client(session.access_token)
    llm = make_chat_llm(False)

    all_rows: list[dict] = []
    print(f"\nDrafting eval ({len(cases)} cases)")
    for case in cases:
        brief_text = format_brief(case.get("brief"))
        rows: list[dict] = []
        for index, section in enumerate(case.get("sections", [])):
            sec = {
                "order_index": index,
                "heading": section.get("heading", ""),
                "clause_type": section.get("clause_type", ""),
                "guidance": section.get("guidance", ""),
            }
            result = await generate_section(sec, brief_text, case["doc_type"], uc, llm)
            row = score_section(result, section)
            rows.append(row)
            all_rows.append(row)
            print(
                f"  {'PASS' if row['pass'] else 'FAIL'} "
                f"[{case['name']}] {section.get('heading')} -> {row['status']} "
                f"(sources={row['n_sources']}, cited={row['cited']})"
            )
        print(f"{case['name']}: {sum(1 for r in rows if r['pass'])}/{len(rows)} pass")

    summary = summarize_runs(all_rows)
    print("\nDrafting eval summary")
    for key, value in summary.items():
        print(f"  {key}: {value:.1%}" if isinstance(value, float) else f"  {key}: {value}")


if __name__ == "__main__":
    asyncio.run(main())

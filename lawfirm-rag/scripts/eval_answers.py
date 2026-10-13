"""
eval_answers.py
===============
Answer-quality check against scripts/golden_sets/synthetic_golden.json.
Where eval_golden.py asks "did the right passage reach the model?", this
asks "is the answer the user sees correct, grounded and properly cited?".

Phase 1 (answer): every answerable question is put through the real chat
pipeline (_prepare_turn + the chat LLM, fresh conversation, cache cleared)
as a real user; answer, sources and retrieved context are saved.
Phase 2 (grade), per answer:
  figures     every number/date in the known answer appears in the answer
  judge       LLM compares answer with the known answer: correct / partial /
              incorrect (short prompt, no context, so it is cheap)
  invented    numbers in the answer that appear nowhere in the retrieved
              context (hallucinated figures)
  quotes      quoted passages (5+ words) checked against the context with
              citations.verifier; anything not "verified" counts as invented
  citation    the answer cites [n] for the expected document; no
              out-of-range citation numbers

Progress is saved after every question; rerun to resume.

    EVAL_EMAIL=... EVAL_PASSWORD=... python scripts/eval_answers.py [--out results.json] [--limit N]
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from supabase import create_client  # noqa: E402

import agents.rag_agent as ra  # noqa: E402
from citations.verifier import VERIFIED, EvidenceRef, verify_citation  # noqa: E402
from config import settings  # noqa: E402
from deps import make_user_client  # noqa: E402
from providers import make_chat_llm  # noqa: E402

GOLDEN = Path(__file__).parent / "golden_sets" / "synthetic_golden.json"
MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december"

JUDGE_PROMPT = """You grade a legal research assistant. Compare the ASSISTANT ANSWER with the KNOWN CORRECT ANSWER to the QUESTION.
- "correct": it states the key facts of the known answer (wording may differ; extra correct detail is fine)
- "partial": some key facts right, but something important missing or vague
- "incorrect": wrong facts, contradicts the known answer, or says it cannot find the answer
Reply with JSON only: {{"verdict": "correct|partial|incorrect", "reason": "<one short sentence>"}}

QUESTION: {question}
KNOWN CORRECT ANSWER: {gold}
ASSISTANT ANSWER: {answer}"""


def norm(text: str) -> str:
    # Thin/no-break spaces and thousands separators vary between answers.
    text = re.sub(r"[   ]", " ", text)
    return re.sub(r"(?<=\d),(?=\d{3})", "", text).lower()


def figures(text: str) -> set[str]:
    """Numbers and day-month-year dates, normalised (1,400,308 -> 1400308)."""
    t = norm(text)
    dates = set(re.findall(rf"\b\d{{1,2}} (?:{MONTHS}) \d{{4}}\b", t))
    nums = {n.rstrip(".") for n in re.findall(r"\b\d+(?:\.\d+)?\b", t)}
    return dates | nums


def quotes(text: str) -> list[str]:
    found = re.findall(r"[\"“]([^\"”]{20,})[\"”]", text)
    return [q for q in found if len(q.split()) >= 5]


async def answer_phase(cases, results, out, uc, user_id) -> None:
    llm = make_chat_llm(False)
    for c in cases:
        if c["query"] in results:
            continue
        ra.clear_answer_cache()
        t0 = time.perf_counter()
        turn = await ra._prepare_turn(str(uuid.uuid4()), c["query"], uc, user_id, None, llm)
        if "final" in turn:
            rec = {"answer": turn["final"]["answer"], "sources": turn["final"]["sources"], "context": "", "path": "no_llm"}
        else:
            try:
                answer = (await asyncio.to_thread(llm.invoke, turn["messages"])).content or ""
            except Exception as exc:  # noqa: BLE001
                answer = f"[generation failed: {exc}]"
            answer, bad = ra._strip_invalid_citations(answer, len(turn["sources"]))
            rec = {"answer": answer, "sources": turn["sources"], "context": turn["messages"][0].content,
                   "path": "llm", "invalid_citations": bad}
        rec["seconds"] = round(time.perf_counter() - t0, 2)
        results[c["query"]] = rec
        out.write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"  answered {len(results)}/{len(cases)} ({rec['seconds']}s) {c['query'][:70]}", flush=True)


def judge(llm, c, answer: str) -> dict:
    for _ in range(4):
        try:
            txt = llm.invoke(JUDGE_PROMPT.format(question=c["query"], gold=c["answer"], answer=answer[:2500])).content
            return json.loads(txt[txt.index("{"): txt.rindex("}") + 1])
        except Exception:  # noqa: BLE001
            time.sleep(5)
    return {"verdict": "ungraded", "reason": "judge failed"}


def grade(cases, results, out) -> None:
    llm = make_chat_llm(False)
    rows = []
    for c in cases:
        r = results[c["query"]]
        ans, ctx = r["answer"], r.get("context", "")
        gold_figs = figures(c["answer"])
        ans_figs = figures(ans)
        cited = {int(n) for n in re.findall(r"\[(\d+)\]", ans)}
        want = f"synthetic_{c['file']}"
        right_n = {s["n"] for s in r["sources"] if s.get("file_id") == want}
        bad_quotes = [
            q for q in quotes(ans)
            if verify_citation(proposition=q, passage_text=ctx or " ",
                               evidence_ref=EvidenceRef(version_id="ctx", version_label="retrieved context")).status != VERIFIED
        ]
        if "judge" not in r:
            r["judge"] = judge(llm, c, ans)
            out.write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
        rows.append({
            "type": c["type"],
            "query": c["query"],
            "verdict": r["judge"].get("verdict", "ungraded"),
            "reason": r["judge"].get("reason", ""),
            "figures_ok": gold_figs <= ans_figs if gold_figs else None,
            # numbers in the answer absent from the retrieved context (ignore
            # citation markers and small list numbers 1-10)
            "invented": sorted(f for f in ans_figs - figures(ctx) - {str(n) for n in cited}
                               if not (f.isdigit() and int(f) <= 10)) if ctx else [],
            "bad_quotes": bad_quotes,
            "cited_right": bool(cited & right_n),
            "has_citation": bool(cited),
            "invalid_citations": r.get("invalid_citations", 0),
            "seconds": r["seconds"],
        })
    report(rows)


def report(rows) -> None:
    n = len(rows)
    v = collections.Counter(r["verdict"] for r in rows)
    fig = [r for r in rows if r["figures_ok"] is not None]
    print(f"\nAnswer quality ({n} questions)")
    print(f"  judge         correct {v['correct'] / n:6.1%}   partial {v['partial'] / n:6.1%}   incorrect {v['incorrect'] / n:6.1%}"
          + (f"   ungraded {v['ungraded']}" if v["ungraded"] else ""))
    print(f"  figures       all expected figures present: {sum(r['figures_ok'] for r in fig) / len(fig):6.1%} of {len(fig)} answers with figures")
    print(f"  invented      answers with a number not in the sources: {sum(bool(r['invented']) for r in rows)}"
          f"   answers with an unverified quote: {sum(bool(r['bad_quotes']) for r in rows)}")
    print(f"  citations     cite the right document: {sum(r['cited_right'] for r in rows) / n:6.1%}"
          f"   any citation: {sum(r['has_citation'] for r in rows) / n:6.1%}"
          f"   invalid numbers removed: {sum(r['invalid_citations'] for r in rows)}")
    secs = sorted(r["seconds"] for r in rows)
    print(f"  latency       median {secs[n // 2]:.1f}s   p90 {secs[int(n * 0.9)]:.1f}s (from this machine)")
    print("\nBy type (judge correct / partial)")
    by = collections.defaultdict(list)
    for r in rows:
        by[r["type"]].append(r["verdict"])
    for t, vs in sorted(by.items()):
        print(f"  {t:11s} n={len(vs):<3} correct {vs.count('correct') / len(vs):6.1%}  partial {vs.count('partial') / len(vs):6.1%}")
    problems = [r for r in rows if r["verdict"] != "correct" or r["invented"] or r["bad_quotes"] or not r["cited_right"]]
    if problems:
        print("\nTo review:")
        for r in problems:
            flags = [r["verdict"]] + (["invented " + ",".join(r["invented"])] if r["invented"] else []) \
                + (["unverified quote"] if r["bad_quotes"] else []) + ([] if r["cited_right"] else ["wrong/no citation"])
            print(f"  [{r['type']}] {r['query'][:80]}\n      {' | '.join(flags)} - {r['reason'][:120]}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("eval_answers_results.json"))
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    cases = [c for c in json.loads(GOLDEN.read_text(encoding="utf-8")) if c["file"]]
    if args.limit:
        cases = cases[: args.limit]
    results = json.loads(args.out.read_text(encoding="utf-8")) if args.out.exists() else {}

    anon = create_client(settings.supabase_url, settings.supabase_anon_key)
    session = anon.auth.sign_in_with_password({"email": os.environ["EVAL_EMAIL"], "password": os.environ["EVAL_PASSWORD"]}).session
    print("Phase 1: answering")
    await answer_phase(cases, results, args.out, make_user_client(session.access_token), session.user.id)
    print("Phase 2: grading")
    grade(cases, results, args.out)


if __name__ == "__main__":
    asyncio.run(main())

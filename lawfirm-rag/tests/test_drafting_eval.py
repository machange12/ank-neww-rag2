"""Offline tests for the Draft Studio golden-set scorers (no live DB / LLM).

Exercises the pure scoring helpers in ``scripts/eval_drafting.py`` and the
shape of the drafting golden set, so a broken expectation or scoring change is
caught without needing credentials or ingested documents.
"""
from __future__ import annotations

import json
from pathlib import Path

from scripts.eval_drafting import (
    cited_numbers,
    citation_validity,
    score_section,
    summarize_runs,
)

GOLDEN = Path(__file__).resolve().parent.parent / "scripts" / "golden_sets" / "drafting_golden.json"


def test_cited_numbers_reads_bracketed_indices():
    assert cited_numbers("See [1] and [2, 3].") == [1, 2, 3]
    assert cited_numbers("No citations here.") == []


def test_citation_validity_flags_out_of_range():
    ok = citation_validity("Grounded in [1] and [2].", 2)
    assert ok["valid"] and ok["cited"] == 2

    bad = citation_validity("Grounded in [1] and [3].", 2)
    assert not bad["valid"] and bad["invalid"] == [3]

    assert not citation_validity("Cites [0].", 5)["valid"]


def test_score_grounded_passes_with_expected_files_and_valid_citations():
    result = {
        "status": "generated",
        "content": "The parties agree. [1]",
        "sources": [{"file_id": "synthetic_lease.txt"}],
    }
    row = score_section(result, {"expect": "grounded", "expected_file_ids": ["synthetic_lease.txt"]})
    assert row["pass"] and row["files_ok"]


def test_score_grounded_fails_when_expected_file_missing():
    result = {
        "status": "generated",
        "content": "The parties agree. [1]",
        "sources": [{"file_id": "clause:abc"}],
    }
    row = score_section(result, {"expect": "grounded", "expected_file_ids": ["synthetic_lease.txt"]})
    assert not row["pass"] and not row["files_ok"]


def test_score_grounded_fails_on_invalid_citation():
    result = {
        "status": "generated",
        "content": "The parties agree. [4]",
        "sources": [{"file_id": "synthetic_lease.txt"}],
    }
    row = score_section(result, {"expect": "grounded", "expected_file_ids": ["synthetic_lease.txt"]})
    assert not row["pass"] and not row["valid_citations"]


def test_score_needs_source_passes_only_when_flagged_and_empty():
    flagged = score_section(
        {"status": "needs_source", "content": "", "sources": []},
        {"expect": "needs_source"},
    )
    assert flagged["pass"]

    filled = score_section(
        {"status": "generated", "content": "Invented text. [1]", "sources": [{"file_id": "x"}]},
        {"expect": "needs_source"},
    )
    assert not filled["pass"]


def test_summarize_runs_aggregates_rates():
    rows = [
        score_section(
            {"status": "generated", "content": "A [1]", "sources": [{"file_id": "f"}]},
            {"expect": "grounded", "expected_file_ids": ["f"]},
        ),
        score_section(
            {"status": "needs_source", "content": "", "sources": []},
            {"expect": "needs_source"},
        ),
    ]
    summary = summarize_runs(rows)
    assert summary["sections"] == 2
    assert summary["pass_rate"] == 1.0
    assert summary["groundedness"] == 1.0
    assert summary["needs_source_correct"] == 1.0
    assert summary["citation_validity"] == 1.0


def test_summarize_runs_handles_empty():
    assert summarize_runs([]) == {"sections": 0}


def test_drafting_golden_set_is_well_formed():
    cases = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert cases, "golden set is empty"

    expectations = set()
    languages = set()
    for case in cases:
        assert case["name"] and case["doc_type"]
        assert case.get("sections"), f"{case['name']} has no sections"
        languages.add(case.get("language"))
        for section in case["sections"]:
            assert section.get("heading")
            expectations.add(section["expect"])

    assert "grounded" in expectations, "golden set needs a grounded case"
    assert "needs_source" in expectations, "golden set needs a must-flag case"
    assert {"en", "sw"} <= languages, "golden set must cover English and Swahili"

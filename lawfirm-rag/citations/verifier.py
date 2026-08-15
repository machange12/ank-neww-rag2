"""Structured citation verification (work package 5).

Input: a proposition/quote candidate, an immutable legal passage/version and an
optional pinpoint. Output: structured, reproducible evidence with a status that
is EXACTLY one of:

    verified / weak / conflicting / unavailable

Rules (conservative by default):
  * ``verified``  — normalized EXACT match on the passage text (whitespace /
    typography / case changes are normalised away first).
  * ``weak``      — high token overlap or alignment score, but not an exact
    match. Never promoted to verified.
  * ``conflicting`` — inferred ONLY from an explicit contradiction/supersedes
    fact supplied by the caller (e.g. a source_relationships.repeals row or a
    newer superseding version). It is NEVER inferred from semantic similarity.
  * ``unavailable`` — everything else (paraphrase, OCR noise, wrong version,
    wrong paragraph, unknown).

The result stores method, scores, thresholds/version and evidence refs so every
outcome is reproducible. A reviewer override is a SEPARATE audit record and
NEVER mutates the original evidence.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from citations.normalize import NormalizedText, normalize_text

VERIFIER_VERSION = "1.0.0"

VERIFIED = "verified"
WEAK = "weak"
CONFLICTING = "conflicting"
UNAVAILABLE = "unavailable"
STATUSES = (VERIFIED, WEAK, CONFLICTING, UNAVAILABLE)

_TOKEN_RE = re.compile(r"[a-z0-9']+", re.UNICODE)


@dataclass(frozen=True)
class Thresholds:
    """Conservative defaults. Only an exact normalized match can be 'verified'."""

    exact_match: bool = True
    weak_min_token_overlap: float = 0.60
    weak_min_similarity: float = 0.55
    min_ngram_n: int = 3
    version: str = "default-conservative"


@dataclass(frozen=True)
class EvidenceRef:
    """Immutable, reproducible reference to the passage/version used."""

    version_id: str
    version_label: str
    passage_id: str | None = None
    locator_kind: str | None = None
    locator_value: str | None = None
    passage_hash: str | None = None
    source_url: str | None = None


@dataclass(frozen=True)
class ReviewerOverride:
    """Audit record of a reviewer's override. NEVER mutates the evidence it
    refers to — it is stored as a separate row (citation_records.reviewer_override)."""

    reviewer_identity: str
    timestamp: str
    reason: str
    prior_status: str
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "reviewer_identity": self.reviewer_identity,
            "timestamp": self.timestamp,
            "reason": self.reason,
            "prior_status": self.prior_status,
            "status": self.status,
        }


@dataclass(frozen=True)
class VerificationResult:
    status: str
    method: str
    scores: dict[str, float]
    thresholds: dict[str, Any]
    verifier_version: str
    evidence_refs: tuple[EvidenceRef, ...]
    normalized_quote: str
    normalized_passage: str
    conflict_sources: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "method": self.method,
            "scores": self.scores,
            "thresholds": self.thresholds,
            "verifier_version": self.verifier_version,
            "evidence_refs": [ref.__dict__ for ref in self.evidence_refs],
            "conflict_sources": list(self.conflict_sources),
            "normalized_quote": self.normalized_quote,
        }


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text))


def _token_overlap(quote_tokens: set[str], passage_tokens: set[str]) -> float:
    if not quote_tokens:
        return 0.0
    return len(quote_tokens & passage_tokens) / len(quote_tokens)


def _n_grams(text: str, n: int) -> set[str]:
    toks = _TOKEN_RE.findall(text)
    if len(toks) < n:
        return set()
    return {" ".join(toks[i : i + n]) for i in range(len(toks) - n + 1)}


def _ngram_overlap(quote_tokens: set[str], passage_tokens: set[str], q_norm: str, p_norm: str, n: int) -> float:
    qg = _n_grams(q_norm, n)
    if not qg:
        return _token_overlap(quote_tokens, passage_tokens)
    pg = _n_grams(p_norm, n)
    return len(qg & pg) / len(qg)


def _alignment_similarity(q_norm: str, p_norm: str) -> float:
    return difflib.SequenceMatcher(None, q_norm, p_norm).ratio()


def verify_citation(
    *,
    proposition: str,
    passage_text: str,
    evidence_ref: EvidenceRef,
    expected_version_id: str | None = None,
    pinpoint: str | None = None,
    thresholds: Thresholds | None = None,
    contradiction: bool = False,
    supersedes: bool = False,
    normalize_kwargs: dict[str, Any] | None = None,
) -> VerificationResult:
    """Verify a quote/proposition against an immutable passage + version.

    ``expected_version_id`` — when supplied, the verifier fails closed
    (``unavailable``) if ``evidence_ref.version_id`` does not match it. This
    makes the "wrong version" case a hard failure instead of a weak match.

    ``contradiction`` / ``supersedes`` — the ONLY way to reach ``conflicting``.
    They must come from explicit source_relationships / version data, never
    from semantic similarity.
    """
    th = thresholds or Thresholds()
    th_dict = th.__dict__.copy()

    quote_norm = normalize_text(proposition, **(normalize_kwargs or {}))
    passage_norm = normalize_text(passage_text, **(normalize_kwargs or {}))

    scores: dict[str, float] = {
        "exact_match": float(quote_norm.normalized == passage_norm.normalized),
        "token_overlap": 0.0,
        "ngram_overlap": 0.0,
        "alignment_similarity": 0.0,
    }

    if expected_version_id is not None and evidence_ref.version_id != expected_version_id:
        return VerificationResult(
            status=UNAVAILABLE,
            method="identity_check",
            scores=scores,
            thresholds=th_dict,
            verifier_version=VERIFIER_VERSION,
            evidence_refs=(evidence_ref,),
            normalized_quote=quote_norm.normalized,
            normalized_passage=passage_norm.normalized,
        )

    conflict_sources = tuple(s for s, flag in (("contradiction", contradiction), ("supersedes", supersedes)) if flag)
    if conflict_sources:
        # Explicit conflict facts take precedence: a conflicting/superseding
        # instrument is never 'verified', even when the texts happen to align.
        return VerificationResult(
            status=CONFLICTING,
            method="explicit_relationship",
            scores=scores,
            thresholds=th_dict,
            verifier_version=VERIFIER_VERSION,
            evidence_refs=(evidence_ref,),
            normalized_quote=quote_norm.normalized,
            normalized_passage=passage_norm.normalized,
            conflict_sources=conflict_sources,
        )

    quote_tokens = _tokens(quote_norm.normalized)
    passage_tokens = _tokens(passage_norm.normalized)
    scores["token_overlap"] = _token_overlap(quote_tokens, passage_tokens)
    scores["ngram_overlap"] = _ngram_overlap(quote_tokens, passage_tokens, quote_norm.normalized, passage_norm.normalized, th.min_ngram_n)
    scores["alignment_similarity"] = _alignment_similarity(quote_norm.normalized, passage_norm.normalized)

    if th.exact_match and quote_norm.normalized == passage_norm.normalized:
        return VerificationResult(
            status=VERIFIED,
            method="normalized_exact_match",
            scores=scores,
            thresholds=th_dict,
            verifier_version=VERIFIER_VERSION,
            evidence_refs=(evidence_ref,),
            normalized_quote=quote_norm.normalized,
            normalized_passage=passage_norm.normalized,
        )

    # Not an exact match. Only a strong alignment may be 'weak'; anything below
    # the conservative thresholds (paraphrase, OCR noise, wrong paragraph) is
    # 'unavailable' and is never promoted.
    if (
        scores["token_overlap"] >= th.weak_min_token_overlap
        or scores["ngram_overlap"] >= th.weak_min_token_overlap
        or scores["alignment_similarity"] >= th.weak_min_similarity
    ):
        return VerificationResult(
            status=WEAK,
            method="alignment_score",
            scores=scores,
            thresholds=th_dict,
            verifier_version=VERIFIER_VERSION,
            evidence_refs=(evidence_ref,),
            normalized_quote=quote_norm.normalized,
            normalized_passage=passage_norm.normalized,
        )

    return VerificationResult(
        status=UNAVAILABLE,
        method="below_thresholds",
        scores=scores,
        thresholds=th_dict,
        verifier_version=VERIFIER_VERSION,
        evidence_refs=(evidence_ref,),
        normalized_quote=quote_norm.normalized,
        normalized_passage=passage_norm.normalized,
    )


def record_reviewer_override(
    evidence: VerificationResult,
    *,
    reviewer_identity: str,
    reason: str,
    status: str,
) -> tuple[VerificationResult, ReviewerOverride]:
    """Record a reviewer override WITHOUT mutating the original evidence.

    Returns the unchanged original evidence plus a separate ``ReviewerOverride``
    audit row (persisted to ``citation_records.reviewer_override``).
    """
    if status not in STATUSES:
        raise ValueError(f"override status must be one of {STATUSES}; got {status!r}")
    override = ReviewerOverride(
        reviewer_identity=reviewer_identity,
        timestamp=datetime.now(timezone.utc).isoformat(),
        reason=reason,
        prior_status=evidence.status,
        status=status,
    )
    return evidence, override
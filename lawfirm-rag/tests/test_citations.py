"""API acceptance tests for citations.verifier + citations.normalize (WP5).

The passage text below is FICTIONAL (a clause from the fictional seed statute).
Cases required by the plan:
  * whitespace-changed exact quote -> verified
  * paraphrase -> not verified
  * OCR/noise not promoted
  * wrong version / wrong paragraph -> fails
  * reproducible outcomes
  * conflicting only from explicit contradiction/supersedes
"""
from __future__ import annotations

from citations.normalize import normalize_text
from citations.verifier import (
    EvidenceRef,
    VerificationResult,
    verify_citation,
    record_reviewer_override,
    CONFLICTING,
    UNAVAILABLE,
    VERIFIED,
    WEAK,
)

PASSAGE_V2_S4 = (
    "Section 4 of the Act requires the Registrar to maintain a national digital "
    "register of all land records, issue digital certificates upon registration, "
    "and publish an annual audit of the register to the public."
)

V2_REF = EvidenceRef(
    version_id="f0000000-0000-0000-0000-00000000001b",
    version_label="2.0",
    locator_kind="section",
    locator_value="4",
    passage_hash="sha256:1234",
)


def test_whitespace_changed_exact_quote_verified():
    quote = (
        "Section   4 of  the Act requires the Registrar to\n"
        "maintain a national digital register of all land records, issue digital "
        "certificates upon registration, and publish an annual audit of the "
        "register to the public."
    )
    result = verify_citation(proposition=quote, passage_text=PASSAGE_V2_S4, evidence_ref=V2_REF, expected_version_id=V2_REF.version_id)
    assert result.status == VERIFIED
    assert result.method == "normalized_exact_match"
    assert result.scores["exact_match"] == 1.0


def test_typographic_chars_normalized_to_verified():
    quote = PASSAGE_V2_S4.replace('"', "\u201c").replace("register,", "register \u2013 ")
    result = verify_citation(proposition=quote, passage_text=PASSAGE_V2_S4, evidence_ref=V2_REF)
    assert result.status == VERIFIED


def test_paraphrase_not_verified():
    paraphrase = (
        "The law obliges the Registrar to keep a nationwide digital land registry "
        "and hand out electronic certificates after registration, along with a "
        "yearly public audit."
    )
    result = verify_citation(proposition=paraphrase, passage_text=PASSAGE_V2_S4, evidence_ref=V2_REF)
    assert result.status == UNAVAILABLE
    assert result.status != VERIFIED


def test_ocr_noise_not_promoted():
    noise = (
        "Section 4 of the Act requires the Reg1strar to m3intain a nation4l digit4l "
        "reg1ster of 4ll l4nd records, issue digit4l cert1ficates upon reg1str4tion."
    )
    result = verify_citation(proposition=noise, passage_text=PASSAGE_V2_S4, evidence_ref=V2_REF)
    # Noise is below the conservative thresholds -> unavailable, never weak/verified.
    assert result.status == UNAVAILABLE


def test_wrong_version_fails_closed():
    quote = "Section 4 of the Act requires the Registrar to maintain a national digital register."
    result = verify_citation(
        proposition=quote,
        passage_text=PASSAGE_V2_S4,
        evidence_ref=EvidenceRef(version_id="f0000000-0000-0000-0000-00000000001a", version_label="1.0"),
        expected_version_id=V2_REF.version_id,
    )
    assert result.status == UNAVAILABLE
    assert result.method == "identity_check"


def test_wrong_paragraph_fails():
    quote = "Any person aggrieved by a decision of the Registrar may appeal to the High Court."
    result = verify_citation(proposition=quote, passage_text=PASSAGE_V2_S4, evidence_ref=V2_REF)
    assert result.status == UNAVAILABLE


def test_weak_for_near_verbatim_with_word_dropped():
    quote = (
        "Section 4 of the Act requires the Registrar to maintain a national digital "
        "register of all land records, issue digital certificates upon registration."
    )
    result = verify_citation(proposition=quote, passage_text=PASSAGE_V2_S4, evidence_ref=V2_REF)
    assert result.status == WEAK


def test_conflicting_only_from_explicit_fact():
    quote = "The Registrar shall maintain a national digital register."
    # Same text would verify, but an explicit supersedes/contradiction fact wins.
    r1 = verify_citation(proposition=quote, passage_text=quote, evidence_ref=V2_REF, supersedes=True)
    assert r1.status == CONFLICTING
    assert r1.conflict_sources == ("supersedes",)

    r2 = verify_citation(proposition=quote, passage_text=quote, evidence_ref=V2_REF, contradiction=True)
    assert r2.status == CONFLICTING
    assert "contradiction" in r2.conflict_sources

    # Without the explicit fact the same text is simply verified.
    r3 = verify_citation(proposition=quote, passage_text=quote, evidence_ref=V2_REF)
    assert r3.status == VERIFIED


def test_reproducible_outcomes():
    r1 = verify_citation(proposition=PASSAGE_V2_S4, passage_text=PASSAGE_V2_S4, evidence_ref=V2_REF)
    r2 = verify_citation(proposition=PASSAGE_V2_S4, passage_text=PASSAGE_V2_S4, evidence_ref=V2_REF)
    assert r1.to_dict() == r2.to_dict()
    assert r1.verifier_version == r2.verifier_version
    assert r1.thresholds == r2.thresholds


def test_reviewer_override_never_mutates_original_evidence():
    result = verify_citation(proposition="nonsense", passage_text=PASSAGE_V2_S4, evidence_ref=V2_REF)
    assert result.status == UNAVAILABLE

    original, override = record_reviewer_override(
        result,
        reviewer_identity="reviewer@firm.law",
        reason="reviewer confirmed the quote against the original gazette",
        status=VERIFIED,
    )
    # Original evidence unchanged.
    assert original.status == UNAVAILABLE
    assert result.status == UNAVAILABLE
    # Override is a separate audit record with identity, timestamp, reason, prior status.
    assert override.status == VERIFIED
    assert override.prior_status == UNAVAILABLE
    assert override.reviewer_identity == "reviewer@firm.law"
    assert override.reason
    assert override.timestamp
    assert override.to_dict()["prior_status"] == UNAVAILABLE


def test_normalize_preserves_original_and_offsets():
    original = "Section 4  of the Act"
    n = normalize_text(original)
    assert n.original == original
    assert n.normalized == "section 4 of the act"
    # offsets map normalized positions back into the original
    start, end = n.span(0, len("section 4 of"))
    assert original[start:end].strip()
    assert n.offsets


def test_normalize_header_footer_removal():
    sourced = "ANNUAL REPORT 2026\n\nBody paragraph one.\nBody paragraph two.\n\nAPPENDIX A"
    n = normalize_text(sourced, strip_headers_footers=True, header_footer_lines=1)
    assert "APPENDIX A" not in n.normalized
    assert "ANNUAL REPORT 2026" not in n.normalized
    assert "body paragraph one." in n.normalized
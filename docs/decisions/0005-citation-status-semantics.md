# ADR-0005 — Citation and Status Semantics: Deterministic and Fail-Closed

**Status:** Accepted (2026)

**Context.** Legal answers depend on "what the law is as of a date" and on
whether a cited passage matches a version. Naive approaches either trust LLM
claims or silently treat unclear labels as current.

**Decision.**

1. **Status semantics** (`corpus/legal_evidence/status.py`): the stored label is
   free text; computed status is deterministic. Draft is never current;
   repealed/superseded is gated by **version dates** (a version still live
   before repeal resolves as current; after repeal nothing resolves). An unclear
   label (`unknown` / `operative`) is never treated as operational, and
   `persistable_operational_status` forces an explicit `in_force`/`commenced`
   for live-but-ambiguous documents. All helpers require the version list.
2. **Citation verification** (`citations/`): `verify_citation` returns one of
   `verified / weak / conflicting / unavailable`. Verification is reproducible
   (same inputs → same result), `conflicting` arises only from explicit
   contradiction/supersession, and identity checks on
   `expected_version_id` fail closed. `record_reviewer_override` never mutates
   the original result.
3. **Normalization** (`citations/normalize.py`) is Unicode-safe, deterministic
   (NFC + typography folding + whitespace collapse + case fold) and returns
   offsets so spans survive normalizing.

**Consequences.** Retrieval scoping (`lock_scope`) requires an explicit
`as_of_date`; expansions inherit the pinned version; citations never silently
"verify". Downstream consumers can rely on a bounded, documented vocabulary.
Residual risk (imperfect classification, non-guarantee of legal accuracy) is
disclosed in the README and threat model.
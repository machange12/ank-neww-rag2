# Legal Evidence Model (WP4) — Design Notes

The `corpus/legal_evidence/` package models the legal corpus as typed,
immutable, as-of-date entities and provides deterministic, conservative status
and pinpoint tooling. It has **no network access** and **never fetches external
law**. ADR-0003 covers the separation rationale.

## Entity model

- **LegalSource** — a registered corpus (e.g. a fictional statutory corpus).
  `source_class` distinguishes `public_primary` from `firm_private`; only a
  registered `public_primary` source can make a document primary law.
- **LegalDocument** — a legal instrument (statute, regulation, statutory
  instrument, memo…). Carries `authority_tier`, `binding_status`,
  `jurisdiction`, `language` and a free `current_status` label.
- **LegalDocumentVersion** — an immutable snapshot of a document
  (`version_label`, `source_hash`, `publication_date`, `commencement_date`,
  `valid_from`/`valid_to`, `repeal_date`, `supersedes_version_id`). Content is
  held as **LegalPassage**s with structural locators.
- **LegalPassage** — a `section`/`paragraph`/… with `rendered_text`,
  `normalized_text`, a content `passage_hash` and `vector_searchable` flag.

## Status tooling (`status.py`)

Status vocabulary (exact strings):
`unknown / draft / commenced / repealed / in_force / not_in_force`.

- **`is_currently_operational(document, as_of_date, versions)`** — `True` when
  an effective version resolves on `as_of_date`. A **draft** is never current.
  A repealed/superseded instrument is **not** current on dates after its repeal
  (no effective version), but **is** current for dates within its operative
  window — repeal is gated by version dates, not by a blanket label check.
- **`effective_status(document, as_of_date, versions)`** — draft → `draft`;
  a version resolves + label `commenced` → `commenced`; a version resolves →
  `in_force`; no version + label repealed/superseded → `repealed`; otherwise →
  `not_in_force`.
- **`persistable_operational_status(...)`** — when a live document has an
  *unclear* label (`unknown` / `operative`), persist an explicit
  `in_force`/`commenced` so downstream consumers never interpret an ambiguous
  label.

All status helpers require the version list to resolve dates; an unclear label
is never treated as operational.

## Temporal / pinpoint tooling (`temporal.py`, `versions.py`)

- **`resolve_version(document, as_of_date, versions)`** — returns the latest
  effective version on the date, or `None` with a reason. Version ordering and
  supersession are resolved deterministically.
- **`lock_scope(scope, document, source, versions, as_of_date)`** — pins
  retrieval to exactly one version for a date, enforcing an **explicit**
  `as_of_date` and rejecting jurisdiction / authority-tier / document-type
  mismatches.
- **`expansion_inherits(locked, expansion_index)`** — every LLM query expansion
  inherits the pinned version/locked scope so no expansion can drift to another
  version.
- **`stable_pinpoint(passage, version)`** — an immutable, reproducible locator:
  `{version_id, version_label, locator_kind, locator_value, passage_hash}`.
- **`is_primary_law` / `primary_law_authority_tier`** — a document is primary
  law only when its source is a registered `public_primary` source AND its
  status is not `unknown`; a `firm_private` source (even with a set
  `authority_tier`) never yields primary law.

## Data policy

`seed.py` contains **fictional** fixtures only. Rights-gated corpora (Kenya Law /
NCLR / Gazette / Parliament) are never auto-ingested; only schema/registry and
explicit manual-seed interfaces exist, and only when rights are held and the
feature is opted in.
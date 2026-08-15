# ADR-0003 — Legal Corpus Is a Separate, Rights-Only Domain

**Status:** Accepted (2026)

**Context.** Firm documents (uploaded/ingested, rights-free) and legal
instruments (rights-gated, e.g. Kenya Law / NCLR / Gazette / Parliament) are
materially different: provenance, licensing, freshness and verification demands.

**Decision.**

1. The legal corpus is modeled separately
   (`corpus/legal_evidence/` + `legal_*` tables in migration 0002) from the firm
   document store.
2. Only **registered sources** (`legal_sources`) with explicit
   `source_class`/`rights_status` can contribute primary law; a `firm_private`
   source never does.
3. External corpora are **never auto-ingested**. Only schema/registry and
   explicit manual-seed interfaces exist, and they require rights to be held and
   the feature to be opted in.
4. Seed content is **fictional** (`corpus/legal_evidence/seed.py`).

**Consequences.** No accidental ingestion of licensed material; primary-law
status is gated on source registration; tests are safe and offline. ADR-0005
governs how statuses are represented.
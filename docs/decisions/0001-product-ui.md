# ADR-0001 — Product is a Chat-First Assistant (RAG), not a Legal Database

**Status:** Accepted (2026)

**Context.** The original n8n workflow exposes RAG chat over firm documents.
Later work added a legal-evidence corpus (`corpus/legal_evidence/`) and citation
tooling. We needed a clear product framing so scope stays bounded.

**Decision.** The product is a **retrieval-augmented chat assistant** over a
firm's documents, with the legal-evidence corpus and citation helpers as
supporting, conservative tooling — not a standalone legal database or a
legal-research service.

**Consequences.** Chat/history/session UX is the primary surface. The corpus and
citation packages are pure, deterministic, fail-closed utilities that enrich
answers; they never autonomously fetch external law. This keeps the product
honest about its non-claims (see README).
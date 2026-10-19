# ADR-0006 — Legal Drafting Assistant ("Draft Studio")

**Status:** Accepted (2026)

**Context.** The firm wants a drafting surface that uses the already-indexed
corpus to help produce first drafts, modelled on Harvey (Assistant → Vault →
in-platform Word editing) and Legora (Editor → playbook checks → deliver in
Word). The house already has grounded retrieval, a hallucination guard,
numbered citations, RLS isolation, SSE streaming and an eval harness. The risk
with a drafting feature is that a generative "assistant" invents contract terms
or legal positions that no firm source supports — exactly the failure the chat
guard exists to prevent, but harder because a draft *looks* like finished,
authoritative work product.

**Decision.**

1. **Drafting is a new surface on the existing RAG core, not a new retrieval
   stack.** `agents/draft_agent.py` reuses `_make_retriever`,
   `_sources_from_docs`, the citation guards and `make_chat_llm` from
   `agents/rag_agent.py`. Retrieval always runs through the caller's own
   PostgREST client (`auth.uid()` RLS); the service-role key stays confined to
   ingest/cleanup/audit/library curation.
2. **Two grounding sources, both access-controlled:** the *curated library*
   (`draft_templates`, `draft_clauses`, `playbooks`, `playbook_rules` —
   read at/below the caller's ceiling + matter grants, written by the service
   role) and the *existing corpus* (`documents`, via `hybrid_search_rls`).
3. **Per-section no-fabrication guard.** A draft is generated section by
   section. If a section's retrieval returns nothing supporting it, the section
   is persisted with `status = 'needs_source'` and a fixed notice — the LLM is
   never asked to fill that gap from general knowledge. The guard covers
   **every** LLM path, including `refine_section`: a `needs_source` section is
   refused (HTTP 422) rather than handed to the model, so no generative route
   can be used to launder an ungrounded section into prose. This is the
   drafting analogue of the chat no-result guard and is the load-bearing safety
   property.
4. **Every generated paragraph is citable.** Sections carry their own
   `sources jsonb`; `[n]` markers use the same numbering contract as chat
   (`rag_agent.py::_build_context_string` / `_sources_from_docs`), so the UI can
   bind each claim to the exact source span (Legora "hover to see the source").
5. **Firm playbook checks are deterministic runs over an LLM judgement, not a
   free-form opinion.** `check_playbook()` evaluates a draft against the
   structured `playbook_rules` (position ∈ required/preferred/fallback/
   prohibited/advisory, severity) and returns flags, each pointing at the
   section and rule it came from.
6. **Drafts are owner-only by default.** `drafts` / `draft_sections` /
   `draft_versions` ownership RLS mirrors `chat_sessions`
   (`user_id_uuid = auth.uid()`), and every mutation stays owner-only. An owner
   may additionally publish a draft **read-only** to a single matter team
   (explicit opt-in; migration `20261013000014`): `drafts.visibility` becomes
   `'matter'` for one non-empty `matter_id`, which adds a parent-inherited
   `SELECT` for callers holding a grant on that matter
   (`auth_can_access_matter_ref`). Members can read the draft and its sections /
   versions / last check but never write, regenerate, re-share or delete it.
7. **Deliverable is DOCX.** Export uses `python-docx`, preserving headings and
   emitting citations as endnotes; markdown/plain text is offered as a fallback.
8. **Language parity.** The drafting prompt and eval set cover English and
   Swahili (house non-negotiable), and the clause retrieval is multilingual.

**Consequences.** Drafting adds eight tables (seven in `20261013000012`,
plus `draft_checks` in `20261013000013`) and two SECURITY INVOKER retrieval
RPCs, matter-scoped **read-only** sharing (`20261013000014`), a `draft_agent` +
`draft` router, and a `DraftPanel`. The per-section guard means a
partially-grounded first draft is the *expected* output — gaps are surfaced,
not hidden. Residual risk (a
grounded-but-wrong clause, imperfect playbook interpretation) is disclosed in
the README's non-claims and the threat model; this remains engineering tooling,
not legal advice.

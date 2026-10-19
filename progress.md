# Draft Studio — Progress & Handoff

**Feature:** a grounded legal **drafting assistant** on top of the existing firm
RAG. Reference products: Harvey (Assistant → Vault → in-Word editing) and Legora
(Editor → playbook checks → deliver in Word).

**Branch state:** work is uncommitted on top of `b1a43e2e`. Next migration slot
`20261013000016`, next ADR `0007`. Live DB is now migrated through
`20261013000015` (was stuck at `0000`–`0009`) and the drafting library is seeded.

**Safety model (non-negotiable):** every section is grounded in the authorized
corpus + curated clause bank. If nothing supports a section, it is persisted as
`needs_source` and the LLM is **never** asked to fill the gap. Retrieval runs
under the caller's RLS only; the service-role key is confined to ingest/cleanup/
audit/library-curation. See `docs/decisions/0006-drafting-assistant.md`.

---

## Decisions locked with the user

| Question | Choice |
|---|---|
| Scope | **Full editor** (brief → skeleton → per-section generation, inline refine, server-side drafts + version history, DOCX export, streaming) |
| Grounding | **Indexed docs + curated template/clause library** |
| Storage | **Server-side, RLS-owned tables** |
| Deliverable | **DOCX first** (`python-docx`) |
| Playbook checks | **Include now** |
| Draft sharing scope | **Read-only** for matter members (owner keeps all writes) |
| Share trigger | **Explicit opt-in** by the owner (drafts are private by default) |

Language parity: English **and** Swahili (house non-negotiable).

---

## Status at a glance

| Phase | Status |
|---|---|
| 1. ADR + migration + schema manifest/tests | ✅ Done |
| 2. `draft_agent` + drafts CRUD + SSE router | ✅ Done |
| 3. Clause/template library tables + seed loader + fused retrieval | ✅ Done (live seeding still needs firm-curated content — see open Q1) |
| 4. Frontend `DraftPanel` + `useDraft` + NAV entry | ✅ Done |
| 5. Inline refine + version history + restore | ✅ Done |
| 6. Playbook `check` endpoint + UI flags | ✅ Done |
| 7. DOCX export | ✅ Done |
| 8. Eval golden set + README/docs sync | ✅ Done |
| 9. Matter-scoped draft sharing (read-only, opt-in) | ✅ Done |

Test suite: **174 passing** (`python -m pytest tests/ -q`). Offline, no network.

---

## What is implemented (by file)

### Phase 1 — schema & decision record
- **`docs/decisions/0006-drafting-assistant.md`** — ADR (context, 8 decisions,
  consequences).
- **`lawfirm-rag/supabase/migrations/20261013000012_drafting.sql`** — 7 tables,
  RLS, and 2 SECURITY INVOKER retrieval RPCs:
  - `draft_templates`, `draft_clauses` (vector(768) + generated `search_tsv`),
    `drafts`, `draft_sections`, `draft_versions`, `playbooks`, `playbook_rules`.
  - RLS: drafts/sections/versions **owner-only** (`user_id_uuid = auth.uid()`,
    mirrors `chat_sessions`); library tables read at/below the caller's ceiling +
    matter grants, written by `service_role`.
  - `match_draft_clauses` + `hybrid_search_clauses` (RRF, mirrors
    `hybrid_search_rls`), granted to `authenticated`.
  - Version marker `'20261013000012'`.
- **`lawfirm-rag/scripts/verify_schema.py`** — added the migration to
  `REQUIRED_MIGRATIONS`, 7 `TableCheck` entries, 2 `FunctionCheck` entries,
  3 `REQUIRED_INDEXES`.
- **`lawfirm-rag/tests/test_schema_manifest.py`** — required-DDL patterns for the
  new tables/functions.
- Verify: `python scripts/verify_schema.py --manifest-only` → `SCHEMA OK`.

### Phase 2 — agent, store, router
- **`lawfirm-rag/search/clauses.py`** — `search_clauses()`: hybrid search over
  `draft_clauses` via RPC, returns LangChain `Document`s tagged
  `metadata.kind="clause"`; returns `[]` on any failure (degrades to
  corpus-only grounding).
- **`lawfirm-rag/agents/draft_agent.py`** — the drafting pipeline. Reuses
  `_make_retriever`, `_build_context_string`, `_sources_from_docs`,
  `_normalize_citations`, `_strip_invalid_citations`, `_strip_markdown` from
  `agents/rag_agent.py`.
  - `default_skeleton(doc_type)`, `parse_skeleton()`, `format_brief()`,
    `build_section_query()`, `is_no_source_answer()`, `_coerce_skeleton()`.
  - `propose_skeleton()` (template skeleton wins; else LLM; else default).
  - `_prepare_section()` — the **no-source guard** (combined corpus+clauses
    empty → `needs_source`).
  - `generate_section()` (non-stream), `stream_generate_section()` (SSE events:
    `token` … then `result`), `refine_section()` (bounded rewrite, no new facts,
    citations preserved).
  - Prompts: `DRAFT_SECTION_TEMPLATE`, `DRAFT_SKELETON_TEMPLATE`,
    `REFINE_SECTION_TEMPLATE`. Constants: `NO_SOURCE_SECTION`,
    `SECTION_RETRIEVE_K=8`, `CLAUSE_RETRIEVE_K=6`.
- **`lawfirm-rag/drafting/store.py`** — RLS-scoped persistence: `create_draft`,
  `list_drafts`, `get_draft`, `update_section`, `snapshot_version` (unused so
  far), `resolve_tenant_id`, `DEFAULT_TENANT_ID`.
- **`lawfirm-rag/routers/draft.py`** — endpoints (all auth via
  `ctx_rbac_from_token` + caller client; generate/refine rate-limited; create/
  generate/refine write audit events):
  - `POST /drafts` (create from brief + optional template → skeleton)
  - `GET /drafts` (list own)
  - `GET /drafts/{id}` (draft + sections + versions)
  - `POST /drafts/{id}/sections/{i}/generate` (**SSE**)
  - `POST /drafts/{id}/sections/{i}` (manual save → status `edited`)
  - `POST /drafts/{id}/sections/{i}/refine`
  - `GET /draft/templates`, `GET /draft/clauses` (library reads)
- **`lawfirm-rag/app.py`** — imports + `include_router(draft.router)`.
- **`lawfirm-rag/tests/conftest.py`** — `FakeTable.update()`, owner map gains
  `drafts`, and a type-safe `order()` sort key fix.
- **`lawfirm-rag/tests/test_drafting.py`** — 14 offline tests: skeleton parsing,
  brief/query helpers, no-source guard (LLM not called), grounding + citation
  hygiene, draft round-trip, owner scoping, manual section edit.
- **`lawfirm-rag/tests/test_service_role_static.py`** — added
  `routers/draft.py`, `agents/draft_agent.py`, `drafting/store.py`,
  `search/clauses.py` to `FORBIDDEN_FILES`.
- Verify: `python -c "import app; print(...)"` lists all `/draft*` routes.

### Phase 3 — curated library (done)
- Tables + `hybrid_search_clauses` live in the migration (above).
- **`lawfirm-rag/drafting/library.py`** — service-role curation loader:
  `seed_templates`, `seed_clauses` (embeds with the ingest-compatible context
  prefix), `seed_playbooks`, `seed_library`. Idempotent (delete-by-natural-key,
  then insert).
- **`lawfirm-rag/drafting/seed_data.json`** — SAMPLE/fictional library: 4
  templates (nda, lease, engagement_letter, memo), 8 clauses, 2 playbooks with
  rules. **Replace with firm-curated content before production.**
- **`lawfirm-rag/scripts/seed_drafting_library.py`** — operator CLI to load the
  library via the service-role client.
- `GET /draft/templates` and `GET /draft/clauses` (hybrid when `?q=`).
- **`lawfirm-rag/tests/test_drafting_library.py`** — 8 offline tests: embed-text
  prefix, template/clause/playbook row shaping, **idempotent delete-then-insert
  by natural key**, embedder skipped for an empty clause list, rule ordering +
  `playbook_id` linkage, `seed_library` summary, and a well-formedness check on
  `seed_data.json` (doc types, positions, severities). Offline via `FakeClient`
  + a stubbed embedder (no model load).

### Phase 4 — frontend (done)
- **`frontend/src/lib/constants.js`** — added `{ icon: FileSignature, label: "Drafting" }`
  to `NAV` (after Documents).
- **`frontend/src/hooks/useDraft.js`** — all Draft Studio network state:
  `loadDrafts`, `loadTemplates`, `createDraft`, `openDraft`, `closeDraft`,
  `generateSection` (SSE reader mirroring `useChat.js`, per-section token
  buffer → result), `saveSection`, `refineSection`. Owns `drafts`,
  `activeDraft` (sections + versions), `templates`, per-section busy indices
  (`generatingIndex`/`refiningIndex`/`savingIndex`) and `streamingText`.
- **`frontend/src/components/DraftPanel.jsx`** — two modes: brief form +
  template picker + draft list; then the section canvas (per-section status
  badge incl. `needs_source`, generate/regenerate, inline edit → save, refine
  box, per-section sources rail via `sourcesFromResponse`). Wraps `Panel`/
  `EmptyState`.
- **`frontend/src/App.jsx`** — `useDraft(...)` wired alongside `useChat`/
  `useDocuments`; `Drafting: <DraftPanel .../>` registered in the `panels` map.
- Verify: `npm run build` → `lawfirm-rag/static/` (1790 modules, no errors).
- Note: the always-on top search bar still shows on the Drafting tab (same as
  every non-Research panel); left for a later shell pass.

### Phase 5 — version history (done)
Granularity decision: **explicit "Save version" button**, plus a **v1 baseline
snapshot on create**. Manual saves/refines do **not** auto-snapshot.
- **`lawfirm-rag/drafting/store.py`** — `snapshot_version()` reworked: derives
  the next number from `_max_version()` (strictly increasing even after a
  restore), inserts the row, bumps `drafts.current_version`, returns the row.
  `create_draft()` writes the v1 baseline and returns `versions`. New
  `get_version()` + `restore_version()` (loads a snapshot back onto the
  sections, then snapshots the restored state as a **new** version — history is
  append-only).
- **`lawfirm-rag/routers/draft.py`** — `POST /drafts/{id}/versions` (snapshot;
  `{label?}`) and `POST /drafts/{id}/versions/{v}/restore`; both audited.
- **`lawfirm-rag/tests/test_drafting.py`** — round-trip now asserts the v1
  baseline; added save-version increment, restore round-trip (append-only) and
  missing-version tests (3 new, all offline).
- **`frontend/src/hooks/useDraft.js`** — `saveVersion(label)`, `restoreVersion(v)`
  (both replace `activeDraft` from the server reply) + `isSavingVersion`/
  `restoringVersion` flags.
- **`frontend/src/components/DraftPanel.jsx`** — `VersionHistory` panel in the
  editor: "Save version" button, per-version list (version, label, timestamp,
  `current` badge) and Restore buttons.
- Verify: `pytest tests/ -q` → 113 passing; `npm run build` clean.

### Phase 6 — playbook checks (done)
Persistence decision: **new `draft_checks` table** (history of checks, not a
column on `drafts`).
- **`lawfirm-rag/supabase/migrations/20261013000013_draft_checks.sql`** — new
  `draft_checks` table (`draft_id`, `playbook_id`, `flags` jsonb, `summary`
  jsonb) + parent-inherited owner RLS (mirrors `draft_versions`) + index +
  version marker `20261013000013`.
- **`scripts/verify_schema.py` / `tests/test_schema_manifest.py`** — migration,
  `TableCheck`, index and DDL pattern registered. `verify_schema.py
  --manifest-only` → `SCHEMA OK`.
- **`agents/draft_agent.py`** — `PLAYBOOK_CHECK_TEMPLATE`;
  `parse_verdicts()` (tolerant JSON-array extraction); `map_rule_verdicts()`
  (deterministic: every rule in the playbook always yields exactly one flag,
  matched by `rule_id` then positionally, metadata attached, invalid/missing
  verdict → `unclear` — so a `prohibited` position can still be a `breach`);
  `summarize_flags()` (counts by status + breaches by severity); `check_playbook()`
  (single LLM call; falls back to all-`unclear` on provider failure).
- **`drafting/store.py`** — `get_playbooks(doc_type)` (`is_active`, doc_type
  or `''`), `get_playbook_rules()` (ordered), `list_playbooks_with_rules()`,
  `save_check()`, `get_latest_check()`; `get_draft()` now attaches `last_check`.
- **`routers/draft.py`** — `GET /draft/playbooks` (+rules) and
  `POST /drafts/{id}/check` (`{playbook_id?}`, auto-selects by doc type when
  omitted; persists the check; audited). No applicable playbook → 200 with an
  explanatory `message` rather than an error.
- **`tests/test_playbooks.py`** — 13 offline tests: verdict→flag mapping,
  positional fallback, invalid-status coercion, **prohibited→breach**, summary
  counts, `check_playbook` (one LLM call / no-rules short-circuit / provider
  failure), playbook filtering + rule ordering + save/read/`last_check`.
- **Frontend** — `useDraft`: `loadPlaybooks(docType)`, `runCheck(playbookId?)`,
  `playbooks`/`checkResult`/`isChecking`; loads playbooks on create/open and
  seeds `checkResult` from `last_check`. `DraftPanel`: `PlaybookCheck` card
  (auto-or-explicit playbook picker + Run check; flags grouped high/medium/low,
  each showing position, the section it points at, status badge and note).
- Verify: `pytest tests/ -q` → 126 passing; `npm run build` clean.

### Phase 7 — DOCX export (done)
Style decision: **minimal default** (no firm letterhead).
- **`lawfirm-rag/requirements.txt`** — added `python-docx==1.2.0`
  (installed into the root `.venv`).
- **`lawfirm-rag/drafting/export.py`** — pure builders over a draft dict:
  `build_docx()` (title, per-section headings + prose paragraphs, a trailing
  **Endnotes — sources** appendix, footer with the draft status/disclaimer),
  `build_markdown()`, `build_text()`, `DRAFT_DISCLAIMER`. python-docx has no
  native endnote API, so the citations are emitted as a labelled
  end-of-document appendix (same information a Word endnote carries).
- **`routers/draft.py`** — `POST /drafts/{id}/export?fmt=docx|md|txt` →
  `Response` with the right media type + `Content-Disposition`; audited.
  Unknown `fmt` → 400. New `_safe_filename()` helper.
- **`tests/test_export.py`** — 5 offline tests: docx is a valid zip, reloads
  with each heading, endnotes + footer disclaimer present, empty-draft path,
  markdown/text structure. `drafting/export.py` added to the service-role
  `FORBIDDEN_FILES`.
- **Frontend** — `useDraft.exportDraft(fmt)` (fetch blob → anchor download),
  `isExporting`; `DraftPanel` header gains DOCX / MD / TXT buttons.
- Verify: `pytest tests/` → 131 passing; `npm run build` clean.

### Phase 8 — eval golden set + docs (done)
- **`lawfirm-rag/scripts/golden_sets/drafting_golden.json`** — 5 cases over 4
  concerns: English grounded sections (`lease` grounded in the synthetic
  Westlands lease via `expected_file_ids`; `nda` grounded via the clause bank),
  an explicit **must-flag unsupported** case (`Swiss Bank Guarantee`), plus
  **Swahili** grounded and unsupported cases (language parity).
- **`lawfirm-rag/scripts/eval_drafting.py`** — drafting eval harness. Pure
  scorers (`cited_numbers`, `citation_validity`, `score_section`,
  `summarize_runs`) + a live runner that calls the real
  `generate_section` under the caller's RLS and reports **groundedness**,
  **citation validity** and **needs-source correctness**.
- **`lawfirm-rag/tests/test_drafting_eval.py`** — 9 offline tests for the
  scorers + a well-formedness check on the golden set (needs a grounded case,
  a must-flag case, and both `en`/`sw`).
- **`lawfirm-rag/search/clauses.py` + migration `20261013000012`** — the
  `hybrid_search_clauses` RPC now also returns **`similarity`**, and
  `search_clauses` drops clauses below `CLAUSE_MIN_SIMILARITY = 0.30`. Without a
  relevance floor the RPC returned the top-N clauses regardless of relevance, so
  an unsupported section could be "grounded" on the nearest-but-irrelevant
  clause and the no-source guard could never fire. The migration now
  `drop function if exists` before `create or replace` (the return-row shape
  changed; grants are re-issued).
- **Root `README.md`** — added a **Draft Studio** feature section and endpoint
  rows, extended the non-claims, added the `00010`–`00013` migration rows and
  ADR `0006` to the decisions list.
- Verify: `pytest tests/` → 148 passing; `verify_schema.py --manifest-only` →
  `SCHEMA OK`.

### Phase 9 — matter-scoped draft sharing (done)
Access model: a draft is **private by default**; the owner can **explicitly**
share it **read-only** with a chosen matter's team. Every mutation stays
owner-only (RLS + a router guard that returns 403).
- **`lawfirm-rag/supabase/migrations/20261013000014_draft_sharing.sql`** —
  `drafts.visibility text not null default 'private'` with check
  `drafts_visibility_check` in (`'private'`,`'matter'`); index
  `drafts_visibility_idx (visibility, matter_id)`. Replaced `drafts_select_own`
  with **owner OR** (`visibility='matter'` AND `matter_id <> ''` AND
  `auth_can_access_matter_ref(matter_id)`); re-asserted the owner-only
  `drafts_insert_own`/`drafts_update_own`/`drafts_delete_own`; new
  parent-inherited SELECT policies `draft_sections_select_shared`,
  `draft_versions_select_shared`, `draft_checks_select_shared`. Version marker
  `20261013000014`.
- **`scripts/verify_schema.py`** — `REQUIRED_MIGRATIONS += "20261013000014"`,
  `drafts` columns += `visibility`, `REQUIRED_INDEXES += "drafts_visibility_idx"`,
  policy lists updated. `--manifest-only` → `SCHEMA OK`.
- **`tests/test_schema_manifest.py`** — visibility column + 3 shared-policy DDL
  patterns added.
- **`authz/service.py`** — new `can_access_matter_ref(user_client, matter_ref)`
  (refuses empty ref, fails closed), used to validate a share target.
- **`drafting/store.py`** — `list_drafts` selects `visibility, user_id_uuid`;
  new `set_draft_visibility(user_client, draft_id, *, visibility, matter_id)`
  (owner-only via RLS; returns the row or `None`).
- **`routers/draft.py`** — `_load_visible_draft` (read) vs `_load_editable_draft`
  (owner-or-403); all write endpoints (generate/save/refine/version save+restore/
  check) use the editable variant, GET + export use the visible variant. New
  `POST /drafts/{draft_id}/share` (validates non-empty + `can_access_matter_ref`,
  audited) and `POST /drafts/{draft_id}/unshare` (→ `private`, `matter_id=''`,
  audited). New `_with_owner()` annotates read responses with `is_owner` so the
  UI can go read-only.
- **`tests/conftest.py`** — `FakeClient`/`FakeTable` now model `matter_grants`
  and per-table insert defaults (`_TABLE_DEFAULTS = {"drafts": {"visibility":
  "private"}}`); the fake applies the matter-share visibility filter on SELECT
  only (writes stay owner-scoped).
- **`tests/test_draft_sharing.py`** — 9 offline tests: visibility defaults,
  owner-only `set_draft_visibility`, reader sees shared-not-private,
  `list_drafts` filtering, `can_access_matter_ref`, and router paths
  (share/unshare round-trip, empty matter → 400, no matter access → 403,
  non-owner share/save → 403).
- **Frontend** — `useDraft`: `shareDraft(matterId)` / `unshareDraft()` +
  `isSharing`. `DraftPanel`: share row in the editor (private → free-text matter
  id + Share; shared → "shared · {matter}" + Make private); a read-only banner
  for non-owners; Generate/Edit/Refine/Save-version/Restore/Run-check hidden when
  `activeDraft.is_owner === false`; a `shared`/`shared with you` badge in the
  draft list. `App.jsx` passes the new props.
- **Root `README.md`** — added the `/drafts/{id}/share` \| `/unshare` endpoint
  row, a Draft Studio `Matter-scoped sharing` row, the `20261013000014` migration
  row, a read-only-sharing line in the non-claims, and refreshed the migration
  range (`0000`–`00014`), test count (157) and architecture diagram.
- Verify: `pytest tests/test_draft_sharing.py -q` → 9 passing;
  `pytest tests/ -q` → **157 passing**; `verify_schema.py --manifest-only` →
  `SCHEMA OK`; `npm run build` → clean (1790 modules → `lawfirm-rag/static/`).

---

## Cross-evaluation pass (2026-10-19)

Independently re-checked the nine phases against the code. Verified: `pytest
tests/ -q` → **162 passing**; `verify_schema.py --manifest-only` → `SCHEMA OK`;
`npm run build` clean. Four real defects found and fixed.

1. **🔴 Safety hole — refine bypassed the no-source guard.** `refine_section()`
   only rejected an *empty* section, but a `needs_source` section's content is
   the non-empty fixed notice, so the router accepted a refine and handed the
   notice to the LLM — exactly the "LLM fills the gap" path ADR-0006 rules out.
   Reproduced with a booby-trapped LLM (called ⇒ fail) before fixing.
   - `agents/draft_agent.py::refine_section` now refuses when
     `status == 'needs_source'` **or** the content is the no-source notice, and
     raises `ValueError` **before** the LLM call (router maps it to **422**).
   - `DraftPanel.jsx` disables the Refine control on a `needs_source` section
     with an explanatory tooltip.
   - Tests: `test_refine_refuses_ungrounded_section_without_calling_llm`,
     `test_refine_endpoint_422_on_needs_source_section`,
     `test_refine_grounded_section_keeps_sources`.
2. **🟠 Misleading response — explicit playbook id that is unknown/inaccessible.**
   `POST /drafts/{id}/check` with a `playbook_id` matched nothing and returned a
   `200 "No playbook applies to this document type."` Now a **404**
   (`test_check_endpoint_404_for_unknown_playbook`); the 200 message is kept only
   for genuine doc-type auto-selection with no applicable playbook.
3. **📄 Docs drift — ADR-0006 was stale.** Decision 6 still said matter sharing
   was "deliberately not enabled", but Phase 9 shipped it. Rewritten to describe
   the explicit, read-only, matter-scoped model (`20261013000014`); decision 3
   now states the guard covers refine too; consequences updated (eight tables,
   `draft_checks`/sharing migrations).
4. **✅ Fixed — `guidance`/`required` are now persisted.** `draft_sections` had
   no such columns, so they were attached in-memory on create but disappeared
   on reload: reopening a draft lost the per-section guidance hint and the
   "required" tag showed on every section.
   - **`migration 20261013000015`** adds `guidance text not null default ''` and
     `required boolean not null default true` (additive + idempotent).
   - `drafting/store.py::create_draft` writes both on insert (the old
     `setdefault`-after-insert hack is gone); `get_draft` returns them via
     `select("*")`.
   - `scripts/verify_schema.py` (migration + both columns) and
     `tests/test_schema_manifest.py` updated; `test_drafting.py` gains
     `test_section_guidance_and_required_persist_through_reload`.
   - **Applied to the live DB** (`20261013000015`) and confirmed live:
     `verify_database` → `SCHEMA OK`; `guidance`/`required` present with the
     defaults above.

---

## OCI hosting (Phase 1 of the Oracle move)

Decision (with the user): split the Oracle move into independent tracks.
**Phase 1 = hosting only on Oracle Cloud (OCI)**, keeping Supabase/Postgres
unchanged. The **database** move to Oracle is a separate multi-week rewrite
(pgvector→Oracle AI Vector Search, tsvector/GIN→Oracle Text, RLS→VPD, and no
PostgREST drop-in for the per-request RLS boundary) — not started.

Deploy artifacts added:
- **`lawfirm-rag/Dockerfile`** — multi-stage: stage 1 builds the Vite frontend,
  stage 2 is a `python:3.11-slim` runtime that installs `requirements.txt`
  (no torch — a hosted embedder is required), runs as non-root, exposes
  `HEALTHCHECK` on `/healthz`, and runs `uvicorn app:app --workers 1`. Layout
  mirrors the checkout (`/srv/lawfirm-rag/lawfirm-rag`) so `config.py`'s
  `ROOT_DIR` and an optional mounted `.env` resolve as on the host.
- **`.dockerignore`** (repo root), **`docker-compose.yml`** (repo root; app
  bound to `127.0.0.1:8000`, `env_file: .env`).
- **`deploy/oci/lawfirm-rag.service`** — systemd unit for the app.
- **`deploy/oci/lawfirm-rag-worker.service`** — systemd unit for the worker
  (nightly cleanup + Drive webhook + manual re-ingest).
- **`deploy/oci/nginx-lawfirm-rag.conf`** — TLS reverse proxy, `proxy_buffering
  off` + 300s timeouts (chat SSE), `client_max_body_size 30m`.
- **`docs/operations/oci-deployment.md`** — full runbook (networking, firewalld/
  SELinux, both deploy paths, prod `.env` checklist, verify, updates/rollback).
  Linked from the README "Further reading".

Key facts captured: the **app** runs the 6-hour Drive poll in-process; the
**worker** runs the nightly cleanup — different jobs, so **run both**, no double
polling. (Earlier drafts wrongly warned the worker also polls Drive; corrected.)

Also fixed en route: **`.env.example`** was stale and would have broken the
deploy — `EMBEDDING_DIM=1536`/`CHUNK_SIZE=750` contradicted the live
`vector(768)` schema and `config.py` (768 / 900 / 80). Rewritten to match reality
(768, the Gemini remote-embedder block, `INGEST_WORKER_TOKEN`, Groq chat model).

Not verified here: the image build (Docker Desktop daemon was stopped on this
machine — `docker build --check` could not connect). `docker compose config`
validated the compose file.

> ⚠️ **Rotate secrets.** Running `docker compose config` printed the full `.env`
> into a session transcript (Supabase service-role + anon keys, JWT secret, DB
> password, Groq/Cohere/Gemini/Fireworks/HF keys, Google OAuth secret + refresh
> token). Treat all as compromised and rotate.

---

## Hosting-migration prep (2026-10-19)

Confirmed with the user: **keep the database on Supabase; OCI is hosting-only.**
That captures *every* hosting benefit (always-on Drive poll, ~24× RAM, no cold
starts, no Render "service-initiated traffic" suspension clause, no 750-hour
cliff) with **none** of the database-rewrite risk. The residual costs are the
generic self-hosting tax (patching/TLS/backups/monitoring, idle-reclamation,
A1 capacity, no managed rollback) — all mitigable, and each has a mitigation in
the cutover doc.

Four follow-ups from the previous session, all done:

1. **Corrected stale free-tier figures.** `oci-deployment.md` claimed "Ampere A1
   free tier (up to 4 OCPU / 24 GB)" and "a 1 OCPU / 6 GB AMD shape". Reality:
   Always Free A1 is **1,500 OCPU-h + 9,000 GB-h/month = 2 OCPU / 12 GB
   always-on** (4/24 is the *Free Trial* allowance), and the Always Free AMD
   shape is **`E2.1.Micro` = 1/8 OCPU / 1 GB** (too small for this app). Added
   the idle-reclamation rule and the A1 "out of host capacity" caveat. Also
   refreshed the runbook's test count (157 → **174**).
2. **New `docs/operations/render-to-oci-cutover.md`** — the ordered runbook this
   was missing: pre-flight (incl. "verify the Docker image, esp. arm64"), OCI
   provisioning, base OS/SELinux, secrets, the production-`.env` gate, deploy
   (compose **or** systemd) + worker, nginx/TLS, **verify before the DNS flip**,
   the DNS cutover, rollback, post-cutover hardening, and a residual-risk table.
   Linked from `README.md` and `oci-deployment.md`.
3. **New `.github/workflows/ci.yml`** — three jobs: the offline pytest suite +
   `verify_schema.py --manifest-only`; a **real multi-arch image build**
   (`linux/amd64` **+ `linux/arm64`**, no push) — the first-ever verification of
   `lawfirm-rag/Dockerfile`, and specifically of the **Arm** target the A1 shape
   needs; and a self-test of the env checker. Addresses the "Docker daemon was
   down, image never built" gap flagged in the OCI section above.
4. **New `lawfirm-rag/scripts/check_production_env.py`** — a stdlib-only static
   gate over the production `.env`: requires `ENVIRONMENT=production`, an
   explicit (non-`*`) `CORS_ORIGINS`, the Supabase keys + `SUPABASE_JWT_SECRET`,
   `POSTGRES_DSN`, `GROQ_API_KEY`, **a hosted embedder** (`OPENAI_API_KEY` or all
   three `REMOTE_EMBED_*` — the image ships no torch), `INGEST_WORKER_TOKEN`
   (opt-in), `EMBEDDING_DIM=768`, plus format/placeholder checks and warnings for
   optional services. `--json` for tooling; exit 1 on errors.
   - `tests/test_check_production_env.py` — **12** offline tests. Suite: **174
     passing** (was 162); counts refreshed in `README.md`, `progress.md`, the
     runbook and the cutover doc.

---

## How to run / verify

```powershell
# from repo root
cd lawfirm-rag
& "..\.venv\Scripts\python.exe" -m pytest tests/ -q
& "..\.venv\Scripts\python.exe" scripts/verify_schema.py --manifest-only

# apply migration (needs DB access) then seed the sample library
& "..\.venv\Scripts\python.exe" scripts/seed_drafting_library.py

# drafting groundedness eval (needs a live, seeded project + credentials)
$env:EVAL_EMAIL="partner@ak.law"; $env:EVAL_PASSWORD="..."
& "..\.venv\Scripts\python.exe" scripts/eval_drafting.py
```

> Note: `.venv` lives at the **repo root**, not in `lawfirm-rag/`. The README's
> `python -m venv .venv` inside `lawfirm-rag` is stale for this checkout.

---

## Remaining work

### Phase 4 — Frontend ✅ (see above)

### Phase 5 — version history ✅ (see above)

### Phase 6 — playbook checks ✅ (see above)

### Phase 7 — DOCX export ✅ (see above)

### Phase 8 — eval + docs ✅ (see above)

### Phase 9 — matter-scoped sharing ✅ (backend + tests + UI + build + README done)

**Session close-out (2026-10-19):**
- ✅ Re-ran `npm run build` (UI changed) → `lawfirm-rag/static/` rebuilds clean
  (1790 modules).
- ✅ Root `README.md`: added `/drafts/{id}/share` + `/unshare` endpoint row, a
  Draft Studio `Matter-scoped sharing` row, the `00014` migration row, and a
  read-only matter-sharing line in the non-claims; refreshed the migration range
  and test count.
- ✅ Cross-confirmed: `pytest tests/ -q` → **157 passing**;
  `verify_schema.py --manifest-only` → `SCHEMA OK`; `import app` lists all 15
  `/draft*` routes including `/share` and `/unshare`; `seed_data.json` holds 4
  templates / 8 clauses / 2 playbooks.
- ✅ **Live DB brought current and smoke-tested** (via `POSTGRES_DSN`):
  - Applied `20261013000010`–`20261013000014` with `scripts/apply_migrations.py`
    (live DB was stuck at `0000`–`0009`; drafting schema was not live).
  - Seeded the library with `scripts/seed_drafting_library.py` → **4 templates,
    8 clauses, 2 playbooks** in `draft_clauses`/`draft_templates`/`playbooks`/
    `playbook_rules`.
  - Live `verify_schema.py` (not just `--manifest-only`) **FAILED** first:
    `index documents_embedding_idx missing` — migrations `00010`/`00011`
    intentionally drop it for `documents_embedding_hnsw`, but the manifest was
    never updated. **Fixed** in `scripts/verify_schema.py`
    (`REQUIRED_INDEXES`: `documents_embedding_idx` → `documents_embedding_hnsw`);
    live run is now `SCHEMA OK`.
  - End-to-end RLS/RPC smoke test as simulated users (transaction rolled back,
    no rows persisted) — **14/14 checks passed**: library reads at/under the
    caller's ceiling; `hybrid_search_clauses` returns `similarity`; an irrelevant
    query scores 0.06 (< `CLAUSE_MIN_SIMILARITY` 0.30); owner reads own / not a
    private draft otherwise; matter member of the shared matter reads a
    `visibility='matter'` draft but a member of a *different* matter does not;
    non-owner UPDATE returns 0 rows; `auth_can_access_matter_ref` lets a partner
    share only to their own matter and a firm-wide role to any.

---

## Open questions / decisions for next session
1. **Seed content:** real firm templates/clauses/playbook positions are needed —
   current `seed_data.json` is fictional placeholder. Who provides them, and in
   what format?
2. ~~DOCX house style~~ — **decided:** minimal default (title/sections/endnotes/
   footer disclaimer); firm letterhead can be layered on later.
3. ~~Draft sharing~~ — **decided & shipped:** matter-scoped, **read-only**,
   **explicit opt-in** (migration `20261013000014`; Phase 9).
4. ~~Playbook check persistence~~ — **decided:** a `draft_checks` table
   (history of checks), migration `20261013000013`.
5. ~~Version snapshot granularity~~ — **decided:** explicit "Save version"
   button + a v1 baseline on create (no auto-snapshot on save/refine).
6. ~~`needs_source` UX~~ — **decided:** show the notice text. The persisted
   `NO_SOURCE_SECTION` message (`agents/draft_agent.py:36`) is already actionable
   ("Add a source document or clause, or draft this section manually") and the
   panel renders `needs_source` sections in amber (`DraftPanel.jsx`). A
   dedicated "insert from clause bank" affordance would need a new backend
   endpoint; deferred as a later enhancement, not a gap.

## Gotchas / notes
- `.venv` is at repo root (see above).
- Migration pre-req: embedding dim is **768**; changing the embedder requires
  re-embedding `draft_clauses` too (same rule as `documents`).
- `drafting/library.py` intentionally uses the service-role client (curation
  write path, like `ingest/*`); it is **not** in `FORBIDDEN_FILES`. Any new
  *user-facing read/write* path must never import it.
- `_prepare_section` does not pass `clause_type_filter` by default (sparse bank
  would return zero); it filters clauses by `doc_type` and relies on query text
  for clause-type relevance.
- Clause grounding has a relevance floor: `search/clauses.py`
  `CLAUSE_MIN_SIMILARITY = 0.30` drops clauses whose vector similarity is too
  low, so an unrelated query returns `[]` from the clause bank and the
  no-source guard in `_prepare_section` can actually fire. Raising/lowering it
  trades recall for stricter grounding; the drafting eval
  (`scripts/eval_drafting.py`) is the way to tune it.
- Frontend has no router/state library/TypeScript — match existing conventions
  (function components, Tailwind inline, hooks own all network state).
- Sharing is **read-only**: the RLS grants matter members SELECT on a shared
  draft (and its sections/versions/checks) only; every write policy is still
  `user_id_uuid = auth.uid()`. `_load_editable_draft` turns a non-owner write
  into a 403. `is_owner` is computed server-side (`_with_owner`) and drives the
  read-only UI; the frontend should treat `is_owner === false` as no-edit.
- `chat.py:60-92` `_resolve_chat_identity` is the chat-specific auth path; the
  drafting router uses the simpler `ctx_rbac_from_token` + `make_user_client`.

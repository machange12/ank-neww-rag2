# ANK Law Firm RAG (`ank neww rag2`)

A secure **retrieval-augmented generation (RAG)** assistant for a single Kenyan
law firm, converted from the n8n `RAG LawFirm 2.3456` workflow into a runnable
Python **FastAPI** backend + **Supabase** (Postgres + row-level security) +
**React (Vite)** frontend.

The engineering focus to date has been **foundation integrity**: every
user-facing read runs through the caller's own authenticated client under RLS,
upload/ingest classification is decided **server-side** (never by the client),
and the legal-evidence/citation tooling is typed, deterministic and conservative.

> **Non-claims.** This is engineering tooling, not a legal research service and
> not a source of legal advice. The legal-evidence and citation helpers are
> conservative and fail closed, but they do not guarantee legal accuracy.

---

## Features

### Core RAG chat

| Feature | Where | Notes |
|---|---|---|
| Secure RAG chat | `POST /lawfirm-chat-trigger-006` | Auth-required; single LLM call; ground-only answer prompt |
| Streaming chat (SSE) | `POST /lawfirm-chat-stream` | Token-streaming Server-Sent Events |
| Intent routing | `agents/rag_agent.py` | One cheap LLM call classifies 5 intents and picks retrieval depth |
| Doc-type scoping | `agents/rag_agent.py` | LLM-free keyword hint (`judgment/contract/statute`) narrows retrieval |
| Hybrid retrieval | `search/hybrid.py` | RRF fusion of vector + BM25 via `hybrid_search_rls`; falls back to pure vector similarity |
| Multi-query + rerank | `agents/rag_agent.py` | Query rewriting (`MultiQueryRetriever`) + optional Cohere rerank (`top_n=5`) |
| Hallucination guard | `agents/rag_agent.py` | Never calls the LLM on zero-doc retrieval; returns a canned no-result response |
| Source citations | `agents/rag_agent.py` | Answers cite `[Title \| Matter ID]`; sources capped at 5 |
| Rate limiting | `ratelimit.py` | Per-user sliding-window token bucket, `Retry-After`, HTTP 429 |

### Security & authorization

| Feature | Where | Notes |
|---|---|---|
| Login → JWT | `POST /auth/login` | Returns `access_token` + role/access_level resolved from DB |
| Role-based access | `rbac/role_matrix.py` | 7 roles → `{level, view_all, privileged, ingest, admin}`; system-prompt prefix |
| DB-backed authorization facts | `authz/service.py` | Role/level/grants read from `user_profiles` / `matter_access`; JWT claims are hints only |
| Server-side upload classification | `authz/policy.py` | Final `access_level = min(ceiling, floor)`; sensitivity markers floor; client hint is never a fact |
| Matter administration checks | `authz/service.py` | `auth_can_administer_matter_ref` RPC gates every ingest target |
| Explicit admin gate | `authz/service.py` | `/admin/users` requires `user_profiles.admin = true`, never a level/role claim |
| Session ownership via RLS | `sessions/manager.py` | `chat_sessions_select_own` / `chat_memory_select_own` keyed to `auth.uid()` |
| Feedback ownership | `POST /feedback` | Session must belong to the caller or the request is 404 |
| Service-role isolation | `search/service_client.py` | No service-role client in user-facing chat/history/search paths (enforced by a static test) |
| Audit trail | `audit/events.py` | Append-only security events: denials, rate limits, classifications, feedback |
| CORS hardening | `config.py` | Production refuses a wildcard allow-origin |

### Ingestion & document management

| Feature | Where | Notes |
|---|---|---|
| Document upload + classification | `POST /documents/upload` | Multipart; level/matter classified server-side; capped at caller ceiling |
| Bulk Drive folder ingest | `POST /documents/ingest-folder` | Per-file server-side classification |
| Single Drive file ingest | `POST /documents/ingest-file` | File-ID driven, classified server-side |
| Drive webhook | `ingest/worker.py` `/drive/webhook` | HMAC channel-token verification; handles remove/trash |
| Scheduled Drive sync | `ingest/scheduler.py` | 6-hour polling; `modifiedTime` fast-path + content-hash skip |
| Nightly orphan cleanup | `cleanup/orphan_finder.py` | Deletes rows whose `file_id` no longer exists in Drive |
| Extraction | `ingest/extract.py` | PDF / DOCX / plain text; repeated header/footer removal, de-hyphenation |
| Legal-aware chunking | `ingest/embed.py` | Kenyan-statute-aware separators (PART/SCHEDULE/WHEREAS/Section N.) |
| Contextual retrieval | `ingest/embed.py` | `Document: <title> / Section: <heading>` prefix on embedding input only |
| Document intelligence | `ingest/classifier.py` | gpt-4o-mini doc-type taxonomy + legal-entity extraction; best-effort |

### Legal evidence & citations (typed, deterministic)

| Feature | Where | Notes |
|---|---|---|
| Legal corpus models | `corpus/legal_evidence/models.py` | `LegalSource/Document/Version/Passage`, authority tiers, rights statuses |
| As-of-date version resolution | `corpus/legal_evidence/versions.py` | Drafts never current; effective windows from dates |
| Conservative operative status | `corpus/legal_evidence/status.py` | Fails closed; unclear labels never operational |
| Temporal locking | `corpus/legal_evidence/temporal.py` | Locks retrieval to exactly one immutable version; stable pinpoints |
| Citation verification | `citations/verifier.py` | Four-state verdict (`verified/weak/conflicting/unavailable`), reproducible |
| Text normalization | `citations/normalize.py` | NFC + typographic folding + offset map for span evidence |
| Reviewer overrides | `citations/verifier.py` | Immutable audit records; never mutates original evidence |
| Fictional seed data | `corpus/legal_evidence/seed.py` | Test-only fixtures (no real external law) |

### Frontend (React + Vite)

| Feature | Notes |
|---|---|
| Login screen | Email/password → JWT stored locally |
| Dashboard shell | Sidebar nav (Research / History / Documents / Matters / Team / Settings) |
| Research panel | Answer with copy / export / save / regenerate; prompt starters |
| Documents panel | Upload (file + matter + access level), list indexed docs, Drive file list + ingest |
| History / Saved | Browser-local persistence (capped at 25) |
| Production build | `vite build` drops straight into `lawfirm-rag/static/` for single-app serving |

### Ops & tooling

- Versioned SQL migrations (`supabase/migrations/` `0000`–`0009`), single source of truth; legacy `sql/*` frozen as deprecated reference.
- Offline pytest suite (64 tests) covering authz policy, upload acceptance, citations, legal evidence, schema manifest, service-role isolation, session ownership.
- Schema manifest verifier (`scripts/verify_schema.py`), migration runner (`scripts/apply_migrations.py`), chat/ingest smoke tests, OAuth token helper.
- Retrieval-quality eval harness (`scripts/eval_retrieval.py`) — golden-set hit-rate / zero-result-rate against a running backend.
- Streamlit test UI (`streamlit_app.py`) with login/chat/upload/admin tabs.

---

## Strengths

- **Defense-in-depth on authorization.** JWT claims are hints; the DB
  (`user_profiles`, `matter_access`) and RLS are the enforcement boundary. Upload
  classification is server-side and conservative (`min(ceiling, floor)`), so a
  sensitive file can never be stamped broadly readable and a caller can never
  write into a matter they don't administer.
- **RLS-native session/feedback ownership.** Chat history, session listing and
  feedback writes all run through the caller's own PostgREST client; ownership
  is enforced by the database (`auth.uid()`), not by parsing session keys.
- **Service-role discipline.** The service-role key is confined to
  ingest/cleanup/audit-write/admin-management; the chat/search/history path never
  uses it, and a static test enforces this.
- **Small, testable pure-logic core.** `authz/`, `citations/`, `corpus/` are
  pure functions with offline tests — fast, deterministic, no network.
- **Conservative legal tooling.** Corpus status and citation verification fail
  closed; an unclear label is never treated as operational.
- **Intent-aware, hallucination-guarded RAG.** One-call intent routing, doc-type
  scoping, multi-query + rerank, and a hard no-result guard.
- **Graceful degradation everywhere.** Retrieval falls back to pure vector
  search; embeddings fall back to a local model; LLM classification failures
  degrade to safe defaults — chat rarely hard-fails.
- **Well-documented.** Architecture docs, security threat model, ADRs, migration
  runbook and a clear non-claims statement.

---

## Weaknesses & known issues

### Recently fixed

The following were identified as weaknesses and have since been resolved:

- **Live database migrated to `0000`–`0009` and verified (`scripts/verify_schema.py` — SCHEMA OK).**
  The live Supabase DB had never had any migration applied (no
  `schema_migrations` table at all) and had evolved a bespoke schema that
  diverged from what migrations 0000–0007 assumed in several concrete ways —
  each one only surfaced by actually attempting the migration against real
  data (22 rows in `documents`, 18 in `document_metadata`, preserved
  throughout):
  - `hybrid_search_rls` only had the old 4-arg signature (no
    `doc_type_filter`), causing `PGRST202` on every doc-type-hinted chat
    query. Fixed by migration `0004`.
  - **Critical, live authorization bypass**: that old 4-arg
    `hybrid_search_rls` overload was `SECURITY DEFINER` with **no
    `access_level`/`matter_id` filtering at all**. Once the new 5-arg
    version existed alongside it, any query that didn't set a doc-type hint
    (most queries) resolved to the *insecure* overload — any authenticated
    user could retrieve any document regardless of clearance or matter
    grants. Migration `0008` drops the insecure overload; Postgres then
    resolves 4-param calls to the secure 5-arg version's default.
  - `hybrid_search_rls`'s own SQL had a real bug, present since it was
    first written (not schema-drift): `RETURNS TABLE(id bigint, ...)`
    makes `id` an implicit PL/pgSQL variable, and the `fused` CTE's
    unqualified `id`/`score` references were ambiguous against it — every
    call errored with "column reference is ambiguous". Never caught before
    because it had never actually been executed. Fixed by migration `0009`.
  - `documents.id` and `document_metadata.id` are `bigint` on this
    deployment, not the `uuid` the migrations declared — `CREATE OR
    REPLACE FUNCTION` can't change a function's return-row shape, so
    `match_documents_rls`/`hybrid_search_rls`/`match_documents` needed
    `DROP FUNCTION IF EXISTS` first, with return type corrected to
    `id bigint`. The app never reads `id` from these RPC results (only
    `content`/`metadata`), so this is safe.
  - `chat_memory` was missing `user_id`/`tenant_id`/`session_uuid`
    entirely, causing every history-persistence insert to 400. Fixed by
    migration `0003` (additive `ADD COLUMN IF NOT EXISTS`).
  - `security_events` was missing `outcome`/`actor_email`/`detail`/
    `ip_address`/`user_id`/`tenant_id` (had `actor` instead of `user_id`),
    so every `audit/events.py` write was silently failing. Fixed
    (migration `0005` now includes the same additive-ALTER pattern).
  - Migration `0003`'s own `chat_sessions.id uuid` column had no
    uniqueness constraint before `chat_memory.session_uuid` tried to
    foreign-key against it — a real bug that would have failed on *any*
    fresh install, not just this one. Fixed with a `CREATE UNIQUE INDEX`.
  - Migration `0003`'s `query_feedback.user_id` backfill assumed `text`
    and used a regex match; live `user_id` was already `uuid`, so the
    regex operator didn't exist for that type. Made the backfill
    type-aware (checks `information_schema.columns` at migration time).
  - Migrations `0006`/`0007` were missing their `schema_migrations`
    version-marker inserts and weren't in `verify_schema.py`'s required
    list — the manifest check had silently never verified them.
  - Migration `0007` (embedding dimension fix) was written assuming the
    live column would be the wrong dimension and unconditionally
    truncated `documents`. It turned out this deployment's `embedding`
    column was **already** `vector(768)` — an unconditional truncate
    would have destroyed 22 real, already-correct rows for no reason.
    Rewritten to check the actual live dimension first and only
    truncate+retype when it's actually wrong.
- **Password exposure during this work — rotate immediately if you
  haven't.** A redaction attempt printed a fragment of the database
  password to a terminal transcript, and a since-superseded password was
  also pasted directly into chat. Both are compromised by virtue of having
  been visible in a session transcript; rotate the Supabase database
  password again (Project Settings → Database → Reset database password)
  independent of anything above.
- **Google Drive OAuth token is expired/revoked (`invalid_grant`)** —
  scheduled Drive sync and nightly cleanup fail every run; manual upload
  ingest is unaffected. This needs an interactive browser consent flow
  (`scripts/get_refresh_token.py`) that can't be run from this environment
  — run it yourself and update `GOOGLE_REFRESH_TOKEN` in `.env`.
- **Embedding/schema dimension mismatch — confirmed, was breaking every
  ingest.** The default embedder path (local `nomic-embed-text-v1.5`, active
  whenever `OPENAI_API_KEY` is unset) outputs 768-dim vectors — confirmed by
  actually loading the model — against a `documents.embedding vector(1536)`
  column. pgvector enforces exact dimension on insert, so every ingest via
  the local embedder was failing. Fixed: both embedder paths are now pinned
  to `settings.embedding_dim` (768) — the local model via `truncate_dim`, the
  OpenAI fallback via `dimensions=` — so switching providers no longer
  requires a schema change. Migration `20260727000007` (destructive: clears
  `documents` and re-types the column; re-ingest required). Also fixed:
  migrations `0006`/`0007` were missing their `schema_migrations` version
  markers and weren't in `scripts/verify_schema.py`'s required-migrations
  list, so the manifest check silently never verified them.
- **Chunking was character-based against a token-context embedder.** 750
  chars/200-char-overlap (`config.py`) was using ~2% of the local embedder's
  8192-token context and paying a CPU embedding pass per tiny chunk, with a
  27% overlap ratio. Switched to token-based chunking (tiktoken
  `cl100k_base` as the splitter's length function) at 900 tokens / 80-token
  overlap (~9%). Chunks now carry a `chunking_version` +
  `chunking_method` tag in their metadata so re-ingests are traceable.
  (`ingest/embed.py`)
- **Optional agentic chunking (off by default).** Added
  `ingest/agentic_chunk.py`: an LLM-boundary pre-pass gated by
  `USE_AGENTIC_CHUNKING`, always falling back to the recursive splitter on
  any failure (LLM error, unparseable response, a chunk that isn't verbatim
  from the source, oversized document) — never blocks ingest. Not enabled
  by default: per the 2025-26 benchmarks, it needs a golden-set A/B eval
  against the real corpus (`scripts/eval_retrieval.py`) before trusting it
  over the tuned recursive splitter, and that eval needs real documents this
  repo doesn't have.
- **Multi-turn chat.** `build_session()` now looks up a client-supplied
  `sessionId` and reuses it (bumping `last_activity_at`) when it names a
  session row the caller owns, instead of always minting a fresh UUID.
  Consecutive turns form one conversation and `run_chat`/`stream_chat` load
  real prior history. (`sessions/manager.py`)
- **Worker `/ingest/manual` was unauthenticated.** Now requires a shared
  `X-Ingest-Worker-Token` header (`INGEST_WORKER_TOKEN` env var), verified with
  a constant-time compare and failing closed (503) if unconfigured.
  (`ingest/worker.py`)
- **Scheduled sync silently downgraded documents.** Now logs a warning when a
  Drive file has no custom `access_level`/`matter_id` properties, matching the
  webhook path's behaviour. (`ingest/scheduler.py`)
- **Unverified-JWT fallback.** Decoding an unverified token when
  `SUPABASE_JWT_SECRET` is unset is now refused outright in production
  (`environment=production`) instead of only warning. (`auth/jwt_validator.py`)
- **Re-ingest deleted before inserting.** `upsert_file()` now inserts the new
  chunks first, then deletes only the file's rows older than that insert (via
  a new `delete_documents_by_file_id_before` RPC, migration `0006`). An
  embed/insert failure now leaves the previous, still-searchable rows in
  place instead of leaving the file with none. (`ingest/store.py`)
- **Unbounded upload size.** Uploads are now read in capped chunks and
  rejected with 413 past `MAX_UPLOAD_BYTES` (default 25 MB) instead of
  buffering an arbitrarily large file into memory. (`app.py`)
- **PostgREST row caps on bulk reads.** `cleanup/orphan_finder.py` now pages
  past Supabase's default `max-rows` (1000) instead of silently missing
  orphans past the first page — this also surfaced a real bug where orphaned
  `document_metadata` rows were deleted by a nonexistent `id` column instead
  of `file_id`, so that half of nightly cleanup was silently a no-op.
  `/documents/intelligence` now has an explicit `.limit()`.
- **Deprecated RLS dead code.** `rls/access_context.py` (confirmed unreferenced
  anywhere) has been removed.
- **Naive sensitivity substring matching.** `authz/policy.py` now matches
  sensitivity markers with word-boundary regex, so e.g. "unrestricted" no
  longer falsely trips the "restricted" ≥3 floor.
- **Streaming turns could be lost.** A partial answer is now persisted to
  `chat_memory` even when the SSE stream fails mid-way, instead of the turn
  being dropped entirely. (`agents/rag_agent.py`)
- **No token-expiry handling; 401s didn't redirect.** The frontend now signs
  the user out (and shows a message) on any 401 via a central `apiFetch`
  wrapper, and proactively signs out when the JWT's `exp` is reached.
- **Fabricated client-side source relevance.** The frontend now renders the
  backend's real `sources` instead of regex-deriving fake relevance bars from
  `[...]` citations in the answer text.
- **Hardcoded `sessionId: "ank-dashboard"`** — replaced with the real
  per-conversation `session_id` the backend returns, which is what makes the
  multi-turn fix above actually work end-to-end from the UI.
- **Hardcoded footer "Active user: 1"** — replaced with the caller's real
  access level from `/auth/login`.
- **Broken `ingest-file` "skipped" check** — fixed to check
  `data.status === "skipped"` (the backend returns `{"status": "skipped"}`,
  never a `skipped` boolean).
- **Unguarded `localStorage` `JSON.parse`** for History/Saved — now goes
  through a `safeParseJSON` helper so a corrupted value falls back to `[]`
  instead of blanking the app.
- **Build hygiene:** `frontend/package.json` now pins exact installed
  versions instead of `"latest"`; the committed merge-conflict markers in
  `.gitignore` are gone; the 8 tracked log files (`uvicorn*.log`, `vite*.log`,
  `worker*.log`, `streamlit*.log`) have been untracked (kept on disk, no
  longer in git) and `.gitignore` now covers `*.log` generally.
- **No logging was configured anywhere.** Neither `app.py` nor
  `ingest/worker.py` ever called `logging.basicConfig`/`dictConfig`, so every
  `logger.info()`/`.debug()` call in the codebase — including all retrieval-
  quality logging — was silently dropped; only WARNING+ reached stderr, via
  Python's unformatted `logging.lastResort` handler. Fixed with
  `logging_setup.configure_logging()`, called first thing in both entrypoints
  (`LOG_LEVEL` env var, default `INFO`).
- **Retrieval quality was unobservable even once logging worked.** The
  hybrid-search-RPC-fails and hybrid-returns-zero-rows fallback paths
  (`search/hybrid.py`, `agents/rag_agent.py`'s `HybridRetriever`) logged
  nothing at all or logged at debug; intent classification and which
  retrieval path served a query were never logged. `_log_retrieval()` now
  emits one structured line per query with intent, retrieve_k,
  doc_type_hint, retrieval path (hybrid vs vector-fallback), doc count, and
  an explicit `zero_result` flag — so fallback rate, zero-result rate, and
  intent distribution are now greppable from logs instead of guessed at.
- **No retrieval-quality eval harness.** Added
  [`scripts/eval_retrieval.py`](lawfirm-rag/scripts/eval_retrieval.py): runs
  a JSON golden set of (query, expected file_id/keywords) pairs against a
  running backend and reports hit-rate / zero-result-rate — a black-box
  check against the real chat response, not an internal probe. Template at
  `lawfirm-rag/scripts/eval_retrieval.example.json`.

### Backend

- (none currently tracked beyond the items above)

### Frontend

- **`Matters`, `Team`, and `Settings` are placeholder-quality** (fake/hardcoded
  data); there is no admin UI despite the backend `/admin/users` endpoint.
  This is a real feature build, not a bug fix, and hasn't been attempted here.
- **History/Saved remain localStorage-only**, capped at 25, with no error
  boundary beyond the `JSON.parse` guard above — by design, not yet backed by
  a server-side store.
- **Hardcoded brand name/copy** ("ANK RAG", "Just Giving Solutions") — cosmetic
  branding, not a functional weakness, left as-is.

### Repo / deployment state

- **`.env` exists in the repo root** (1.8 KB) with live credentials; it is
  gitignored but be careful never to commit it. The committed
  `tests/integration_test.py` contains real-looking passwords.
- **Port/config drift:** README says the main app runs on 8000, the frontend
  config points at 8001, and repo logs show it ran on 8002.
- **The remote Supabase database appears to be on a legacy schema.** As of the
  last integration run, `query_feedback` and `schema_migrations` tables were
  missing and `chat_sessions` lacked `tenant_id`/`user_id_uuid` — i.e. versioned
  migrations `0001`–`0006` have **not** been applied. Consequence: chat sessions
  can't be persisted (the endpoint falls back to a derived key), `/sessions` and
  `/feedback` fail their checks, and a direct-Postgres migration run is blocked
  by DNS from the dev machine. **Apply the migrations via the Supabase CLI
  (`supabase link` + `supabase db push`) or the SQL Editor before relying on
  session/history/feedback features.** These three items require live infra
  access and have not been addressed in this pass.

---

## Architecture

```
FastAPI app (app.py)  +  ingest worker (ingest/worker.py)
        │                       │
        │ user client (JWT)     │ service-role (ingest/cleanup/audit-write)
        ▼                       ▼
   Supabase PostgREST        ingest/worker paths
        │                       │
        ▼                       ▼
   Postgres + RLS + migrations 0000..0009
        ├─ chat_sessions / chat_memory        (ownership RLS: user_id_uuid)
        ├─ user_profiles / matter_access      (authorization facts)
        ├─ documents / document_metadata      (retrieval, matter + level RLS)
        ├─ legal_* corpus tables              (legal evidence, migration 0002)
        └─ audit_security_events              (append-only)
```

Pure-logic packages (no DB): `authz/`, `citations/`, `corpus/legal_evidence/`,
`rbac/role_matrix.py`.

## Tech stack

- **Backend:** FastAPI 0.111, Pydantic v2, python-jose, supabase-py, LangChain 1.x
  (openai / groq / cohere / huggingface), LangGraph, APScheduler, pypdf.
- **LLMs:** Groq `llama-3.3-70b-versatile` (primary), OpenAI `gpt-4.1-mini`
  (fallback); OpenAI `text-embedding-3-small` or local `nomic-embed-text-v1.5`.
- **Storage:** Supabase (Postgres + pgvector, PostgREST + RLS), Google Drive API.
- **Frontend:** React 19, Vite, Tailwind CSS, lucide-react (no router, no state
  library, no TypeScript).
- **Tests:** pytest (64 offline tests); `requests`-based integration script.

## Repo layout

```
lawfirm-rag/            FastAPI backend, packages, tests, migrations, scripts
frontend/               React + Vite dashboard (builds to lawfirm-rag/static)
docs/                   architecture, decisions (ADRs), operations, security
.env / .env.example     configuration (do not commit .env)
```

## Quick start

```bash
cp .env.example .env              # fill keys (Supabase, OpenAI/Groq, Drive OAuth)
cd lawfirm-rag
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000            # chat + API
uvicorn ingest.worker:app --host 0.0.0.0 --port 8001  # Drive webhook + scheduler

cd ../frontend
npm install
npm run dev                       # dev UI on 5173
npm run build                     # production build -> lawfirm-rag/static
```

Full setup (Supabase schema, Google Drive OAuth, users, access levels) is in
[`lawfirm-rag/SETUP.md`](lawfirm-rag/SETUP.md).

## Testing

```bash
cd lawfirm-rag
.venv\Scripts\python -m pytest tests/ -q                # 64 offline tests
.venv\Scripts\python scripts/verify_schema.py --manifest-only
.venv\Scripts\python tests/integration_test.py           # live backend checks
```

The integration script exercises a running backend on `http://localhost:8001`
(login/roles, chat session creation, session isolation, admin access, upload
access control, feedback ownership) and prints PASS/FAIL per test.

## Endpoints (summary)

| Method | Path | Purpose |
|---|---|---|
| GET | `/healthz` | Liveness |
| POST | `/auth/login` | Email/password → JWT |
| POST | `/lawfirm-chat-trigger-006` | Secure RAG chat |
| POST | `/lawfirm-chat-stream` | Streaming chat (SSE) |
| POST | `/feedback` | Feedback (ownership-checked) |
| POST | `/documents/upload` | Upload + server-side classification |
| GET | `/documents` | List documents |
| GET | `/documents/intelligence` | Document intelligence |
| GET | `/sessions` | Session history (RLS) |
| GET/POST | `/admin/users` | List / create users (explicit DB admin only) |
| GET | `/documents/drive-files` | Google Drive file list |
| POST | `/documents/ingest-folder` | Bulk Drive ingest |
| POST | `/documents/ingest-file` | Single Drive file ingest |
| (worker) POST | `/drive/webhook` | Drive push event |
| (worker) POST | `/ingest/manual` | Manual folder re-ingest |

## Migrations

`supabase/migrations/` is the single source of truth. Apply in order; each
migration is idempotent (`create table if not exists`, `create or replace
function`, `if not exists` grants) so re-running is safe. Back up before
applying and roll back by restoring the backup — see
[`docs/operations/migrations-and-rollbacks.md`](docs/operations/migrations-and-rollbacks.md).

| File | Purpose |
|---|---|
| `20260727000000_baseline.sql` | documents/chunks, hybrid + vector search RPCs, `set_access_context`, ivfflat index |
| `20260727000001_tenant_and_matter_access.sql` | tenants, `user_profiles`, `matter_access` |
| `20260727000002_legal_evidence.sql` | legal sources/documents/versions/passages |
| `20260727000003_chat_sessions_ownership.sql` | `chat_sessions` ownership (`user_id_uuid`) + backfill note |
| `20260727000004_jwt_authorization_retrieval.sql` | `auth.uid()`-derived RLS + RPC predicates; JWT-claims auth; `set_access_context` deprecated |
| `20260727000005_audit_security_events.sql` | `audit_security_events` append-only table + policies |
| `20260727000006_atomic_reingest.sql` | `delete_documents_by_file_id_before` — lets re-ingest insert new chunks before deleting stale ones, instead of the reverse |
| `20260727000007_embedding_dimension_768.sql` | Fixes `documents.embedding` dimension → 768 to match the local embedder's actual output. Conditional: only truncates+retypes `documents` if the live dimension is actually wrong. |
| `20260727000008_drop_insecure_hybrid_search_overload.sql` | **Security fix**: drops a live, pre-existing 4-arg `hybrid_search_rls` overload that was `SECURITY DEFINER` with no `access_level`/`matter_id` filtering — an authorization bypass for any query without a doc-type hint. |
| `20260727000009_fix_hybrid_search_ambiguous_id.sql` | Fixes an ambiguous `id` column reference in `hybrid_search_rls`'s `fused` CTE (real bug present since the function was first written; every call errored). |

## Deployment / operations checklist

- Apply migrations 0–9 and back up before applying (see the table above and
  [`docs/operations/migrations-and-rollbacks.md`](docs/operations/migrations-and-rollbacks.md)).
- Set `CORS_ORIGINS` to an explicit allow-list in production (wildcard is rejected).
- Set `SUPABASE_JWT_SECRET` in production — without it, the app/worker refuse
  to accept tokens rather than falling back to unverified decoding.
- Set `INGEST_WORKER_TOKEN` before relying on the ingest worker's
  `/ingest/manual` endpoint; it is required and the endpoint fails closed
  (503) without it.
- Never place the service-role key where user-facing chat/history reads can
  use it. It is allowed only in `ingest/*`, `cleanup/`, `audit/events.py` and
  `authz/service.py` admin management (enforced by a static test — see
  [`lawfirm-rag/tests/test_service_role_static.py`](lawfirm-rag/tests/test_service_role_static.py)).
- If you set `OPENAI_API_KEY` (switching the embedder from local
  nomic-embed-text-v1.5 to OpenAI), no schema change is needed — both paths
  are pinned to `EMBEDDING_DIM` (768 by default). If you change
  `EMBEDDING_DIM` itself, you must also re-run migration `0007`'s column
  re-type (with a new dimension) and re-ingest.
- `USE_AGENTIC_CHUNKING` is off by default; see the Weaknesses entry above
  before enabling it in production.
- Keep `.env` out of version control (see `.gitignore`); no secrets are committed.

## Further reading

- [`docs/architecture/foundation-integrity.md`](docs/architecture/foundation-integrity.md)
- [`docs/architecture/legal-evidence-model.md`](docs/architecture/legal-evidence-model.md)
- [`docs/security/threat-model.md`](docs/security/threat-model.md)
- [`docs/decisions/`](docs/decisions/) (ADRs)
- [`lawfirm-rag/CHANGELOG.md`](lawfirm-rag/CHANGELOG.md) / [`lawfirm-rag/CHANGES.md`](lawfirm-rag/CHANGES.md)
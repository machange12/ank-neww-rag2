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

- Versioned SQL migrations (`supabase/migrations/` `0000`–`0005`), single source of truth; legacy `sql/*` frozen as deprecated reference.
- Offline pytest suite (64 tests) covering authz policy, upload acceptance, citations, legal evidence, schema manifest, service-role isolation, session ownership.
- Schema manifest verifier (`scripts/verify_schema.py`), migration runner (`scripts/apply_migrations.py`), chat/ingest smoke tests, OAuth token helper.
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

### Backend

- **Multi-turn chat is effectively broken.** `build_session()` mints a new UUID
  on every request and the app never maps a client-supplied `sessionId` back to a
  persisted session (the supplied id is only used in the DB-write-failure
  fallback). Consecutive turns don't form one conversation unless the client
  re-sends the server-returned `session_id`, and `run_chat` loads history for an
  empty fresh session each time. (`sessions/manager.py:56`, `app.py:331`)
- **Worker `/ingest/manual` is unauthenticated.** `ingest/worker.py:78` has no
  token or channel-token check; any reachable caller can trigger a full folder
  re-ingest with arbitrary `access_level`/`matter_id`.
- **Scheduled sync can silently downgrade documents.** If a Drive file has no
  custom properties, `ingest/scheduler.py:72` stamps it
  `access_level=1`/`matter_id=""` with **no warning** (the webhook path logs
  loudly by comparison).
- **Unverified-JWT fallback.** `auth/jwt_validator.py:28` decodes tokens
  **without signature verification** when `SUPABASE_JWT_SECRET` is missing (only
  a `warnings.warn`). Misconfiguration → forged claims.
- **Re-ingest deletes before inserting.** `ingest/store.py` deletes a file's
  vectors before the new insert; an insert/embed failure leaves the file
  unsearchable until a later run (no transaction).
- **Unbounded upload size.** `app.py:606` reads the whole file into memory with
  no cap (memory-DoS vector).
- **PostgREST row caps on bulk reads.** Cleanup and `/documents/intelligence`
  select without `.limit()`; Supabase's default `max-rows` (1000) silently
  truncates on large corpora.
- **Deprecated RLS layer is mostly dead code.** `rls/access_context.py` is never
  called; migration 0004 deprecates `set_access_context` but the package remains.
- **Sensitivity markers are naive substring matches** (`authz/policy.py`), so
  words like "restricted"/"confidential" anywhere in a doc force level ≥3
  (conservative but can mis-stamp innocuous files).
- **Streaming turns can be lost.** If the SSE stream fails mid-way, the
  user/assistant turn isn't persisted (`agents/rag_agent.py:425`).

### Frontend

- **No token-expiry handling; token in `localStorage`** — stale/expired tokens
  only fail on the next API call and 401s never redirect to login; localStorage
  is XSS-susceptible.
- **Source relevance is fabricated client-side.** The backend returns real
  `sources` but the frontend ignores them and re-derives fake relevance bars by
  regex-stripping `[...]` citations from the answer text.
- **`Matters`, `Team`, and `Settings` are placeholder-quality** (fake/hardcoded
  data); there is no admin UI despite the backend `/admin/users` endpoint.
- **History/Saved are localStorage-only**, capped at 25, with unguarded
  `JSON.parse` and no error boundary (a corrupt value blanks the app).
- **Hardcoded values:** `sessionId: "ank-dashboard"`, brand name, footer
  "Active user: 1". The `ingest-file` "skipped" check is broken because the
  backend returns `{"status":"skipped"}` not `skipped:true`.
- **Build hygiene:** `package.json` pins everything to `"latest"` (non-reproducible);
  **merge-conflict markers are committed** into `.gitignore` and `vite*.log`; log
  files are tracked in git.

### Repo / deployment state

- **`.env` exists in the repo root** (1.8 KB) with live credentials; it is
  gitignored but be careful never to commit it. The committed
  `tests/integration_test.py` contains real-looking passwords.
- **Port/config drift:** README says the main app runs on 8000, the frontend
  config points at 8001, and repo logs show it ran on 8002.
- **The remote Supabase database appears to be on a legacy schema.** As of the
  last integration run, `query_feedback` and `schema_migrations` tables were
  missing and `chat_sessions` lacked `tenant_id`/`user_id_uuid` — i.e. versioned
  migrations `0001`–`0005` have **not** been applied. Consequence: chat sessions
  can't be persisted (the endpoint falls back to a derived key), `/sessions` and
  `/feedback` fail their checks, and a direct-Postgres migration run is blocked
  by DNS from the dev machine. **Apply the migrations via the Supabase CLI
  (`supabase link` + `supabase db push`) or the SQL Editor before relying on
  session/history/feedback features.**

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
   Postgres + RLS + migrations 0000..0005
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

`supabase/migrations/` is the single source of truth (apply in order `0000`–`0005`).
Legacy `sql/*` files are frozen reference only. See
[`docs/operations/migrations-and-rollbacks.md`](docs/operations/migrations-and-rollbacks.md).

## Further reading

- [`docs/architecture/foundation-integrity.md`](docs/architecture/foundation-integrity.md)
- [`docs/architecture/legal-evidence-model.md`](docs/architecture/legal-evidence-model.md)
- [`docs/security/threat-model.md`](docs/security/threat-model.md)
- [`docs/decisions/`](docs/decisions/) (ADRs)
- [`lawfirm-rag/CHANGELOG.md`](lawfirm-rag/CHANGELOG.md) / [`lawfirm-rag/CHANGES.md`](lawfirm-rag/CHANGES.md)
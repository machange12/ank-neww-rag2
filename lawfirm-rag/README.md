# lawfirm-rag

A secure retrieval-augmented generation (RAG) assistant for a single law firm,
converted from the n8n "RAG LawFirm 2.3456" workflow into a runnable Python
(FastAPI) + Supabase (Postgres/RLS) + React (Vite) project.

The focus of the current work is **foundation integrity**: every user-facing read
runs through the caller's own authenticated client under row-level security,
classification of uploaded/ingested material is decided **server-side**, and the
legal corpus + citation tooling are typed, deterministic and conservative.

> **Non-claims.** This is engineering tooling, not a legal research service and
> not a source of legal advice. Legal-evidence and citation helpers are
> conservative and fail closed, but they do not guarantee legal accuracy. See
> [Security model & non-claims](#security-model--non-claims).

---

## Capability status

| Capability | Status |
|---|---|
| Secure RAG chat (`/lawfirm-chat-trigger-006`) | **available** |
| Streaming chat (`/lawfirm-chat-stream`, SSE) | **available** |
| Hybrid search (vector + BM25 via `hybrid_search_rls`) | **available** (schema in migration 0004) |
| Session history list (`/sessions`) | **available** |
| Feedback collection (`/feedback`, ownership-checked) | **available** |
| Server-side upload classification (`/documents/upload`) | **available** |
| Bulk Drive ingest with per-file classification (`/documents/ingest-folder`, `/documents/ingest-file`, worker `/drive/webhook`) | **available** |
| Admin user management (`/admin/users`) | **available** (explicit DB admin only) |
| Security audit trail (`audit/events.py`) | **available** |
| Legal evidence corpus model + status/pinpoint tooling (`corpus/legal_evidence/`) | **available** (package + migration 0002) |
| Citation normalization + verification (`citations/`) | **available** |
| External legal corpus ingestion (Kenya Law / NCLR / Gazette / Parliament) | **not enabled without rights** (never auto-ingested) |
| Offline pytest suite (64 tests), schema manifest check, frontend build | **run in this workspace, passing** |
| Live database migration apply / integration test against a real Supabase project | **not run here** — needs live infra access; see [Weaknesses & known issues](../README.md#weaknesses--known-issues) in the root README |

Status vocabulary: **available** = implemented and exercised by offline tests;
**not enabled without rights** = deliberately disabled, requires licensed/rights
data and explicit opt-in; **planned** = specified but not yet implemented.

---

## Why it is different (from the n8n version)

- **No service-role key on user-facing reads.** Chat, history and session reads
  use the user's signed-in client under RLS. The service-role key is confined to
  the ingest/cleanup paths and the audit write path.
- **Classification is server-side.** The client can no longer stamp its own
  `access_level`/`matter_id` as an authorization fact. The effective level is
  computed from the DB profile, DB grants and a content sensitivity floor.
- **Authorization facts come from the database.** `role`, `access_level` and
  `matter_ids` in JWT/app_metadata are treated as **hints only**; authoritative
  checks read `user_profiles`, `matter_access` and the
  `auth_can_administer_matter_ref` RPC.
- **One migration system.** `supabase/migrations/` is the single source of truth
  (timestamps `20260727000000`…`20260727000005`). The legacy `sql/` files are
  frozen reference (see `sql/DEPRECATED.md`).
- **Deterministic, conservative legal tooling.** Corpus status and citation
  verification are pure functions that fail closed; an unclear label is never
  treated as operational.

---

## Architecture

```
FastAPI app (app.py)  +  worker (ingest/worker.py)
        │                       │
        │ user client (JWT)     │ service-role (ingest/cleanup/audit-write)
        ▼                       ▼
   Supabase PostgREST        ingest/worker paths
        │                       │
        ▼                       ▼
   Postgres + RLS + migrations 0..5
        │
        ├─ chat_sessions / chat_memory        (ownership RLS: user_id_uuid)
        ├─ user_profiles / matter_access      (authorization facts)
        ├─ documents / document_metadata      (retrieval, matter + level RLS)
        ├─ legal_sources/documents/versions   (legal evidence corpus)
        └─ audit_security_events              (immutable append-only)

Packages (pure logic, no DB):
  authz/       authorization decisions + admin checks
  corpus/legal_evidence/   typed legal corpus + status/pinpoint tooling
  citations/   text normalization + citation verification
  audit/       security-event writes
```

- **Frontend** (`frontend/`) builds to `lawfirm-rag/static/` and is served by the
  FastAPI app.
- **JWT → identity.** `auth/jwt_validator.py` decodes the Supabase JWT. The
  decoded claims are hints; DB-backed `user_profiles`/`matter_access` are the
  authorization facts.

---

## Local development

Prerequisites: Python 3.11+, Node 18+, a Supabase project with the migrations
applied.

```bash
# Backend
cp .env.example .env            # fill keys
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt

uvicorn app:app --host 0.0.0.0 --port 8000            # chat web
uvicorn ingest.worker:app --host 0.0.0.0 --port 8001  # drive + cleanup worker

# Frontend (dev)
cd frontend
npm install
npm run dev

# Frontend (production build -> ../lawfirm-rag/static)
npm run build
```

### Offline tests and schema checks

```bash
cd lawfirm-rag
.venv\Scripts\python -m pytest tests/ -q                     # 64 tests, offline
.venv\Scripts\python scripts/verify_schema.py --manifest-only  # SCHEMA OK
.venv\Scripts\python -m compileall -q app.py config.py authz audit \
    ingest sessions agents search corpus citations scripts
```

---

## Database migrations

`supabase/migrations/` is the single source of truth. Apply in order:

| File | Purpose |
|---|---|
| `20260727000000_baseline.sql` | documents/chunks, hybrid + vector search RPCs, `set_access_context`, ivfflat index |
| `20260727000001_tenant_and_matter_access.sql` | tenants, `user_profiles`, `matter_access` |
| `20260727000002_legal_evidence.sql` | legal sources/documents/versions/passages |
| `20260727000003_chat_sessions_ownership.sql` | `chat_sessions` ownership (user_id_uuid) + backfill note |
| `20260727000004_jwt_authorization_retrieval.sql` | `auth.uid()`-derived RLS + RPC predicates; JWT-claims auth; `set_access_context` deprecated |
| `20260727000005_audit_security_events.sql` | `audit_security_events` append-only table + policies |
| `20260727000006_atomic_reingest.sql` | `delete_documents_by_file_id_before` — lets re-ingest insert new chunks before deleting stale ones, instead of the reverse |

Backup before applying and roll back by restoring the backup; each migration is
idempotent (`create table if not exists`, `create or replace function`,
`if not exists` grants) so re-running is safe. See
[`docs/operations/migrations-and-rollbacks.md`](../docs/operations/migrations-and-rollbacks.md).

---

## Security model & non-claims

- **Threat model:** see
  [`docs/security/threat-model.md`](../docs/security/threat-model.md).
- **Authorization facts** (`user_profiles.admin`,
  `user_profiles.firm_wide`/`access_level`, `matter_access.can_administer`) come
  from the database only. Client-supplied `role`/`access_level`/`matter_ids` are
  hints and never gate access.
- **Classification** of uploads/ingest is computed server-side
  (`authz.policy.classify_upload`): final level = `min(ceiling, floor)` where
  ceiling is the caller's authority and floor is a deterministic sensitivity
  marker floor. A sensitive file can never be made broadly readable.
- **Audit** events (denials, rate limits, feedback, classifications) are written
  through `audit/events.py`.
- **Non-claims:** the deterministic sensitivity floor and the conservative legal
  status/citation helpers are engineering guardrails, not proof of legal
  correctness or a substitute for professional legal research.
- **Data-source policy:** the corpus is seeded with **fictional** content only.
  Kenya Law / NCLR / Gazette / Parliament material is rights-gated and is **never
  auto-ingested**; schema/registry/manual-seed interfaces exist but require
  explicit rights and opt-in.

---

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
| GET | `/admin/users` | List users (admin) |
| POST | `/admin/users` | Create/update user (admin) |
| GET | `/documents/drive-files` | Drive files |
| POST | `/documents/ingest-folder` | Ingest Drive folder |
| POST | `/documents/ingest-file` | Ingest single Drive file |
| (worker) POST | `/drive/webhook` | Drive push event |
| (worker) POST | `/ingest/manual` | Manual ingest |

---

## Deployment / operations checklist

- Apply migrations 0–6 and back up before applying (see
  [`docs/operations/migrations-and-rollbacks.md`](../docs/operations/migrations-and-rollbacks.md)).
- Set `CORS_ORIGINS` to an explicit allow-list in production (wildcard is
  rejected).
- Set `SUPABASE_JWT_SECRET` in production — without it, the worker/app now
  refuse to accept tokens rather than falling back to unverified decoding.
- Set `INGEST_WORKER_TOKEN` before relying on the ingest worker's
  `/ingest/manual` endpoint; it is required and the endpoint fails closed
  (503) without it.
- Never place the service-role key where user-facing chat/history reads can use
  it. It is allowed only in `ingest/*`, `cleanup/`, `audit/events.py` and
  `authz/service.py` admin management.
- Keep `.env` out of version control (see `.gitignore`); no secrets are
  committed.

## Further reading

- [`docs/architecture/foundation-integrity.md`](../docs/architecture/foundation-integrity.md)
- [`docs/architecture/legal-evidence-model.md`](../docs/architecture/legal-evidence-model.md)
- [`docs/security/threat-model.md`](../docs/security/threat-model.md)
- [`docs/operations/migrations-and-rollbacks.md`](../docs/operations/migrations-and-rollbacks.md)
- [`docs/decisions/`](../docs/decisions/) (ADRs)
# lawfirm-rag

Conversion of the n8n RAG LawFirm 2.3456 workflow into a runnable Python project.

## Layout

```
app.py                       # FastAPI web: /auth/login, /lawfirm-chat-trigger-006
ingest/worker.py             # FastAPI worker: /drive/webhook + nightly cleanup
auth/jwt_validator.py        # Supabase JWT verify + user ctx
rbac/role_matrix.py          # 6-role matrix
rbac/checker.py              # system prompt builder
sessions/manager.py          # session_key = user_id__session_id
rls/filter_builder.py        # RLS payload
rls/access_context.py        # RPC set_access_context
agents/rag_agent.py          # ChatOpenAI + SupabaseVectorStore + Cohere rerank + PostgresChatMemory
ingest/{downloader, extract, embed, store, drive_webhook}.py
cleanup/orphan_finder.py     # Nightly vector + metadata orphans
search/{supabase_client, service_client}.py
scripts/chat_smoketest.py    # Partner vs Associate comparison
sql/                         # Reference SQL (apply in Supabase, see below)
```

## Run

```bash
cp .env.example .env  # fill in keys
pip install -r requirements.txt

uvicorn app:app --host 0.0.0.0 --port 8000            # chat web
uvicorn ingest.worker:app --host 0.0.0.0 --port 8001  # drive + cleanup worker
```

## Supabase Setup

### 1. Apply the SQL files in `sql/` in this order

In the Supabase Dashboard → **SQL Editor** → **New query**:

| # | File                       | Purpose                                                                    |
|---|----------------------------|----------------------------------------------------------------------------|
| 1 | `schema.sql`               | Tables (`documents`, `document_metadata`, `chat_memory`) + ivfflat index.  |
| 2 | `set_access_context.sql`   | RPC that stamps the transaction with role / matter IDs (transaction-scoped). |
| 3 | `match_documents_rls.sql`  | RLS-aware vector search (`SECURITY INVOKER`) + `match_documents` + delete helper. |
| 4 | `rls_policies.sql`         | Enable + force RLS on `documents`/`document_metadata`, add policies.       |

### 2. Roles live in Supabase Auth (no SDK)

Dashboard → **Authentication** → **Users** → open a user → **User Metadata** → `app_metadata` JSONB:

```json
{ "role": "senior_associate", "access_level": 3, "matter_ids": ["M-2024-118", "M-2025-001"] }
```

Keys read by the Python pipeline: `role`, `access_level`, `matter_ids`.

---

## How to Fix Role-Based Access on Supabase (full step-by-step)

This is the real fix. Apply in order. Each step has a **verify** command — don't advance until verify passes.

### Step 1 — Disable the service-role shortcut

If anything (n8n, the old agent, an export script) hits Supabase with the **service_role** key to read `documents`, RLS is bypassed and nothing you do in SQL will help. Audit:

- n8n credentials named `newFirmRag` (Supabase node) and `OpenAi account 4/5` use the project.
- Confirm the chat path runs as anon/authenticated **only**.

### Step 2 — Apply `schema.sql`

Creates:

- `documents (id, content, metadata, embedding vector(1536), access_level int, matter_id text)`
- `document_metadata (id, file_id, file_title, url, mime_type, ingested_at)`
- `chat_memory (id, session_id, message jsonb, created_at)`
- ivfflat cosine index on `embedding`.

**Verify:**

```sql
select table_name from information_schema.tables
 where table_schema='public' and table_name in ('documents','document_metadata','chat_memory');
```

Expect 3 rows.

### Step 3 — Apply `set_access_context.sql`

Creates RPC:

```sql
public.set_access_context(p_access_level int, p_matter_ids text[], p_view_all boolean, p_user_id uuid, p_role text)
```

with:

- `SECURITY DEFINER` so any caller can write the GUCs.
- `set_config(..., true)` for each GUC → **transaction-scoped**, safe with PgBouncer in transaction mode.
- Granted to `authenticated, anon` only. Revoked from `public`.

**Verify (paste as any anon role user):**

```sql
select  current_setting('lawfirm.access_level', true);
select set_access_context(3, array['M-2024-118'], false, auth.uid(), 'senior_associate');
select  current_setting('lawfirm.access_level', true);
-- first call: NULL or ''; after RPC: '3'
```

### Step 4 — Apply `match_documents_rls.sql`

Replaces `match_documents_rls(...)` with:

- `SECURITY INVOKER` → policies + the function's own predicate both apply.
- Reads `current_setting('lawfirm.access_level' / 'matter_ids' / 'view_all')`.
- Filter: `access_level <= v_access  AND  (v_view_all OR matter_id = ANY(v_matters))`.

Also creates:

- `match_documents(...)` (no RLS) for the ingest path — grant to `service_role` only.
- `delete_documents_by_file_id(text)` — `SECURITY DEFINER`, granted to `service_role` only.

**Verify:**

```sql
select proname, prosecdef from pg_proc where proname in ('match_documents_rls','match_documents','set_access_context','delete_documents_by_file_id');
-- prosecdef: t for set_access_context & delete_documents_by_file_id ; f for match_documents_rls & match_documents
```

### Step 5 — Apply `rls_policies.sql`

- `ALTER TABLE public.documents ENABLE ROW LEVEL SECURITY`
- `ALTER TABLE public.documents FORCE ROW LEVEL SECURITY` (applies even to table owner).
- Policy `auth can select documents` reads the same GUCs → user + policy cannot disagree.
- Policy `service writes documents` gives the worker unrestricted write.
- Same for `document_metadata`.

**Verify:**

```sql
select relname, relrowsecurity, relforcerowsecurity
  from pg_class where relname in ('documents','document_metadata');
-- relrowsecurity=t, relforcerowsecurity=t
```

### Step 6 — Set `app_metadata` on test users

Use **Dashboard → Auth → Users → User Metadata (app_metadata)**:

| User                    | app_metadata                                                                                       |
|-------------------------|----------------------------------------------------------------------------------------------------|
| `partner@ak.law`        | `{"role":"partner","access_level":4,"matter_ids":["M-2024-118"]}`                                  |
| `assoc@ak.law`          | `{"role":"associate","access_level":2,"matter_ids":["M-2024-001"]}`                                 |
| `managing_partner@…`    | `{"role":"managing_partner","access_level":5,"matter_ids":[]}` (view_all implied)                  |

### Step 7 — Connection pool mode

- If you front Postgres with **PgBouncer in transaction mode**: keep `set_config(..., true)` as written (transaction-scoped). **Do not** switch to `false`.
- If you front Postgres with **PgBouncer in session mode**: still safe because each user Postgres connection gets its own backend.
- If you connect **directly** to Postgres: also safe.
- **Never** run with `pool_mode = statement`. Statement-level reset leaks GUCs between requests.

### Step 8 — Run the smoke test

```bash
cd lawfirm-rag
python scripts/chat_smoketest.py \
  partner@ak.law <pw> \
  assoc@ak.law   <pw> \
  "Summarise matter M-2024-118's status"
```

Expected:

- `partner@ak.law` returns authoritative content on M-2024-118 (its matter).
- `assoc@ak.law` either refuses (matter not in its list) or returns no textual answer.

You can also confirm directly in SQL as a non-admin user:

```sql
select count(*) from public.documents
 where metadata->>'file_id' is not null;
-- partner sees all rows; associate sees only its matter rows
```

### Roles reference

| role             | level | view_all | privileged | ingest | admin |
|------------------|-------|----------|------------|--------|-------|
| managing_partner | 5     | yes      | yes        | yes    | yes   |
| partner          | 4     | no       | yes        | yes    | no    |
| senior_associate | 3     | no       | no         | yes    | no    |
| associate        | 2     | no       | no         | no     | no    |
| paralegal        | 2     | no       | no         | no     | no    |
| legal_secretary  | 1     | no       | no         | no     | no    |
| it_admin         | 5     | yes      | no         | yes    | yes   |

### Endpoints

| Method | Path                          | Purpose                              |
|--------|-------------------------------|--------------------------------------|
| POST   | `/auth/login`                 | Exchange email/password → JWT        |
| POST   | `/lawfirm-chat-trigger-006`   | Secure RAG chat (Authorization header) |
| POST   | `/drive/webhook` (worker)     | Google Drive push event ingest       |
| GET    | `/healthz`                    | Liveness                             |

### Chat request

```http
POST /lawfirm-chat-trigger-006
Authorization: Bearer <jwt>
Content-Type: application/json

{ "chatInput": "Summarise the latest NDA.", "sessionId": "abc-123" }
```

### Chat response

```json
{ "output": "..." }
```

---

## Why this is safer than the n8n version

- n8n's `Set_Access_Context` ran inside the workflow executor — single shared process context. The Python version calls a per-user `set_access_context` RPC on the user's signed-in Supabase client, so each request gets transaction-scoped GUCs.
- `match_documents_rls` is `SECURITY INVOKER` — both the function's predicate and the table's RLS policy filter with the same GUCs, so policy and SQL cannot disagree.
- The service-role key is loaded only inside the worker (`search/service_client.py`). The chat app uses the user's anon/key JWT exclusively.

---

## Troubleshooting

| Symptom                                                                          | Cause                                                               | Fix                                                                                  |
|----------------------------------------------------------------------------------|---------------------------------------------------------------------|-------------------------------------------------------------------------------------|
| `set_access_context` 403                                                          | `set_config` not granted to anon                                     | Re-run `grant execute … to authenticated, anon` from `set_access_context.sql`.      |
| Every user gets the same rows                                                     | Chat path is using `service_role`                                    | Stop using service-role on chat; switch to anon or per-user JWT.                    |
| All rows return for an associate                                                  | `force row level security` not on                                    | `alter table public.documents force row level security;`                            |
| GUCs leak between users                                                           | PgBouncer in `statement` mode                                        | Switch to `transaction` or `session` mode.                                         |
| `permission denied for function match_documents` for the worker                   | Worker call needs service-role                                        | Use `SUPABASE_SERVICE_ROLE_KEY` in worker env, not anon key.                        |
| Agent retrieves privileged docs for a `senior_associate` user                     | RBAC prompt not blocking; relying on retrieval only                  | System prompt + `privileged: false` on `senior_associate` — add matter‑scope check. |
| Empty replies across the board                                                    | `chat_memory` not in DB                                              | `chat_memory` is created by `schema.sql`, not separately.                           |

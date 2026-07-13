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

uvicorn app:app --host 0.0.0.0 --port 8000       # chat web
uvicorn ingest.worker:app --host 0.0.0.0 --port 8001   # drive + cleanup worker
```

## Supabase (you apply)

Apply the SQL files in `sql/` in order:

1. `schema.sql`
2. `set_access_context.sql` (RPC used by Python `set_access_context`)
3. `match_documents_rls.sql`
4. `rls_policies.sql`
5. `chat_memory.sql`

Configure user roles in **Auth → Users → User Metadata `app_metadata` JSONB**:

```json
{ "role": "senior_associate", "access_level": 3, "matter_ids": ["M-2024-118"] }
```

`app_metadata` keys consumed: `role`, `access_level`, `matter_ids`.

### RBAC fix checklist

1. RLS is enabled and **forced** on `documents` (`rls_policies.sql`).
2. `set_access_context` RPC exists, is `SECURITY DEFINER`, uses `set_config(..., true)` for transaction-pool safety, and is granted to `authenticated, anon` only.
3. `match_documents_rls` is `SECURITY INVOKER` and reads `current_setting('lawfirm.access_level' / 'matter_ids' / 'view_all')` to filter.
4. Chat path uses the **user's signed-in JWT**, not `service_role`. The Python chat (`app.py`) calls `set_access_context` via a per-user Supabase client (`make_user_client`). The service-role key is loaded only in the `ingest` worker.
5. Connection pool: PgBouncer in **session-pool** mode is required if you pool, otherwise the `true` flag inside `set_access_context` already scopes the config to the transaction.
6. Smoke test:  `python scripts/chat_smoketest.py partner@… pw1 assoc@… pw2` — partner should see more rows than associate.

### Roles

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
| POST   | /auth/login                   | Exchange email/password for JWT      |
| POST   | /lawfirm-chat-trigger-006     | Secure RAG chat (Authorization header) |
| POST   | /drive/webhook (worker)       | Google Drive push event ingest       |
| GET    | /healthz                      | Liveness                             |

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

## Why this is safer than the n8n version

- n8n's `Set_Access_Context` ran in the workflow's executor context (single shared process). The Python version runs inside the *user's* signed-in Postgres connection (via `set_access_context` RPC), so each request gets its own scoped GUCs.
- The vector Search function is `SECURITY INVOKER`, so RLS applies uniformly across both the SQL predicates inside the function AND the policies on the table.
- The service-role key never leaves the worker process (see `search/service_client.py`).

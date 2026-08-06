# newFirmRAG — Setup Guide

Secure RAG knowledge-management system for Kenyan law firms. FastAPI +
Supabase (pgvector) + LangChain.

---

## 1. Prerequisites

- **Python 3.11+**
- A **Supabase project** with pgvector enabled (it is enabled by default on
  new projects; otherwise run `CREATE EXTENSION IF NOT EXISTS vector;`).
- A **Google Cloud project** with the **Drive API** enabled.
- An **OpenAI API key** for embeddings + chat (e.g. `text-embedding-3-small`
  and `gpt-4.1-mini`).

---

## 2. Supabase setup

1. Open your Supabase project dashboard → **SQL Editor**.
2. Paste the entire contents of `schema.sql` and run it. This creates the
   `documents`, `document_metadata` and `chat_sessions` tables, enables RLS,
   and installs the helper functions (`delete_documents_by_file_id`,
   `hybrid_search_rls`, `set_access_context`) plus all indexes.

Env vars needed in `.env`:

| Variable | Purpose |
| --- | --- |
| `SUPABASE_URL` | Project URL, e.g. `https://xxxx.supabase.co` |
| `SUPABASE_ANON_KEY` | Public anon key (used for login / user-signed clients) |
| `SUPABASE_SERVICE_ROLE_KEY` | Service role key (bypasses RLS; used for ingestion writes) |
| `SUPABASE_JWT_SECRET` | JWT secret for local token verification |
| `POSTGRES_DSN` | Direct Postgres DSN, e.g. `postgres://postgres:postgres@localhost:5432/postgres` |

Copy `.env.example` to `.env` and fill in real values.

---

## 3. Google Drive setup

1. In the [Google Cloud Console](https://console.cloud.google.com), enable the
   **Google Drive API**.
2. Create **OAuth 2.0 credentials** of type **Desktop app**.
3. Download the client secret JSON and copy the client ID / secret into `.env`:
   - `GOOGLE_OAUTH_CLIENT_ID`
   - `GOOGLE_OAUTH_CLIENT_SECRET`
4. Generate a refresh token:

   ```bash
   python scripts/get_refresh_token.py
   ```

   A browser window will open asking you to authorize Drive read-only access.
   Copy the printed token and paste it into `.env` as:

   ```bash
   GOOGLE_REFRESH_TOKEN=<token>
   ```

5. Set `DRIVE_FOLDER_ID` to the Drive folder you want to ingest. Files in that
   folder (and their embedded chunks) are pulled in during ingestion. Per-file
   `access_level` / `matter_id` can be set via Drive custom file properties.

---

## 4. OpenAI setup

- Set `OPENAI_API_KEY` in `.env`.
- Optionally set `COHERE_API_KEY` to enable reranking of retrieved chunks.

---

## 5. Running locally

```bash
cd lawfirm-rag
pip install -r requirements.txt
uvicorn app:app --reload --port 8000
# or for the Streamlit UI:
streamlit run streamlit_app.py
```

The API is then available at `http://localhost:8000` (docs at
`http://localhost:8000/docs`).

---

## 6. First ingest

Run the ingest smoke test, which logs in, ingests the configured Drive folder,
and verifies the chat endpoint returns sources:

```bash
python scripts/ingest_smoketest.py
```

It reads `BASE_URL`, `EMAIL`, `PASSWORD` and `MATTER_ID` from the environment
(meaningful defaults for localhost). Alternatively, trigger ingestion manually
from the UI — **Partners and above only**.

---

## 7. Adding users

Use the **Admin tab** in the Streamlit UI (requires `access_level` 5) or add
users directly in the Supabase dashboard (Authentication → Users). Set
`user_metadata.access_level` to 1–5 based on role:

- **1** = clerk
- **2** = (associate junior) — see access levels table
- **3** = associate
- **4** = (senior associate) — see access levels table
- **5** = partner / admin

---

## 8. Access levels

| Level | Meaning |
| --- | --- |
| 1 | Public documents (firm-wide, non-sensitive) |
| 2 | Internal |
| 3 | Confidential |
| 4 | Privileged |
| 5 | Partner only |

Access is enforced twice: the app computes an `access_level` ceiling from the
user's role, and the database RLS policies re-check it against every row. Rows
are additionally scoped by `matter_id` — a user only sees their own matters
plus firm-wide documents (`matter_id = ''`).

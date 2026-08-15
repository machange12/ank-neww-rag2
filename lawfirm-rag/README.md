# lawfirm-rag (backend)

FastAPI backend for the ANK Law Firm RAG. **Full project documentation —
features, architecture, security model, weaknesses/known issues, migrations,
and the complete endpoint list — lives in the
[root README](../README.md).** This file is a quick reference for working
inside this directory; it deliberately doesn't duplicate that content.

## Run from this directory

```bash
python -m venv ../.venv          # or reuse the repo-root .venv
../.venv/Scripts/activate        # Windows
pip install -r requirements.txt

uvicorn app:app --host 0.0.0.0 --port 8000            # chat + API
uvicorn ingest.worker:app --host 0.0.0.0 --port 8001  # Drive webhook + scheduler
```

Environment variables are read from `../.env` (see `../.env.example`). Full
setup — Supabase schema, Google Drive OAuth, users, access levels — is in
[`SETUP.md`](SETUP.md).

## Tests and checks

```bash
../.venv/Scripts/python -m pytest tests/ -q                     # offline test suite
../.venv/Scripts/python scripts/verify_schema.py --manifest-only  # SCHEMA OK
../.venv/Scripts/python -m compileall -q app.py deps.py config.py authz audit \
    ingest sessions agents search matters routers corpus citations scripts
../.venv/Scripts/python tests/integration_test.py   # needs a running backend + live Supabase
```

## Layout

```
app.py              FastAPI app: wiring, CORS, static serving, exception handling
deps.py              Shared auth/JWT/RBAC context helpers used by every router
routers/             One module per domain: auth, chat, documents, admin
matters/             Matter ref/UUID resolution + administer checks (data access)
search/              Supabase clients, hybrid retrieval, document list reads
ingest/, cleanup/     Drive ingest pipeline, worker, nightly orphan cleanup
authz/, rbac/, auth/  Authorization decisions, role matrix, JWT validation
sessions/             Chat session + history persistence
corpus/, citations/   Legal evidence model, citation verification
audit/                Security event trail
tests/                Offline pytest suite (see root README for the full list)
supabase/migrations/  Versioned SQL migrations (see root README for the table)
```

For everything else — capability status, strengths/weaknesses, architecture
diagram, security model, deployment checklist, tech stack — see the
[root README](../README.md).

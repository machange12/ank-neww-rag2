---
name: pre-push-checks
description: Run the law-firm RAG's checks before committing or pushing — offline pytest suite, schema manifest, frontend build, and a diff review for secrets and service-role leaks. Use before any push, or when the user asks to run the tests / check it still works.
---

# Checks before pushing (ank-neww-rag2)

## Backend (from `lawfirm-rag/`, venv active)

```bash
python -m pytest tests/ -q                       # offline suite, includes the service-role static test
python scripts/verify_schema.py --manifest-only  # migrations match the expected manifest
```

On Windows the venv python is `.venv\Scripts\python`.

## Frontend (if `frontend/` changed)

```bash
cd frontend && npm run build   # builds into lawfirm-rag/static — must finish with no errors
```

## Live checks (only when a backend is running on :8001)

```bash
python tests/integration_test.py                                            # auth, session isolation, admin, uploads
python scripts/eval_retrieval.py <email> <password> scripts/golden_sets/<set>.json   # retrieval hit rate
```

Run the retrieval eval when changing anything in `agents/`, `search/`, `ingest/` or the search RPCs, and compare
hit rate / zero-result rate with the previous run.

## Review the diff before pushing

- No `.env`, keys, DSNs or tokens.
- The service-role key is only used in `ingest/*`, `cleanup/`, `audit/events.py` and `authz/service.py`; user-facing
  reads use the caller's own client (enforced by `tests/test_service_role_static.py` — never weaken that test).
- New schema lives in a new file under `supabase/migrations/` (see `supabase-deploy`).
- Upload/ingest classification stays server-side.

## On failure

Show the output, find the root cause, fix it. Never skip, delete or loosen a test to get green.

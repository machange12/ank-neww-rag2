# Threat Model — lawfirm-rag

Scope: the FastAPI backend, Supabase (Postgres + RLS + RPCs), and the
legal-evidence/citation tooling. The React frontend is a thin client; the same
threats apply to its network path.

## Assets

- **A1** Authorization facts: `user_profiles`, `matter_access`.
- **A2** Firm documents and chunks (`documents`, `document_metadata`).
- **A3** Chat sessions and history (`chat_sessions`, `chat_memory`).
- **A4** Legal corpus (`legal_*`) and citations.
- **A5** Audit trail (`audit_security_events`).
- **A6** Service-role key and JWT secrets (`.env`).

## Trust boundaries

```
   Client  ──JWT──►  FastAPI  ──user client──►  PostgREST/RLS  ──►  Postgres
                              └──service-role──► (ingest/cleanup/audit-write
                                                 + admin management only)
```

- The client is **not trusted**: any field it sends (role, access_level,
  matter_ids, access_level hint) is a hint or is ignored.
- RLS is the enforcement boundary for reads; the backend policy functions are
  the enforcement boundary for classification/ingest decisions.

## Threats and mitigations

| T# | Threat | Mitigation |
|----|--------|------------|
| T1 | User reads another user's chat/session history | RLS `chat_sessions_select_own` / `chat_memory_select_own` keyed to `auth.uid()`; session listing and feedback ownership checks use the caller's client. |
| T2 | Client stamps its own `access_level`/`matter_id` on upload | `classify_upload` computes the level server-side (`min(ceiling, floor)`); client value is a hint recorded for audit. |
| T3 | User ingests into a matter they do not administer | `auth_can_administer_matter_ref` RPC + `administered` verdict threaded into `classify_upload`; firm-pool requires `firm_wide`. |
| T4 | Client claims admin by sending a high role/level | `/admin/users` requires `user_profiles.admin = true` (DB); JWT role is a hint. |
| T5 | Sensitive file made broadly readable | `sensitivity_floor` over content; final level never below floor, never above ceiling. |
| T6 | Service-role key used on user-facing reads | Static policy: no service-role client in `app.py` chat/history/search/session paths; enforced by `tests/test_service_role_static.py`. |
| T7 | GUC/context leakage between users | Supabase RLS policies derive identity from `auth.uid()` (JWT) directly (migration 0004); legacy `set_access_context` is deprecated. |
| T8 | Wildcard CORS in production | `cors_allow_origins` raises if `"*"` in production (tested). |
| T9 | Tampered legal-status claims / wrong-law hallucination | Corpus status is deterministic and fails closed; unclear labels are never operational; draft/repealed gated by version dates. |
| T10 | Privilege escalation via feedback/history writes | Ownership checks on feedback; history inserts stamped with the caller's `user_id`/`tenant_id`. |
| T11 | Secrets leakage | `.env` gitignored; no secrets in commits; service-role confined to allowed modules. |
| T12 | Audit trail tampering | `audit_security_events` is append-only (RLS/grants); writes go through the dedicated audit path. |

## Residual risks

- **R1** Deterministic classification is a guardrail, not perfect
  classification: `sensitivity_floor` can under-classify novel phrasing. Logged
  and auditable, but a human review step for level ≥ floor is recommended.
- **R2** Legal-evidence/citation helpers cannot guarantee legal accuracy; they
  fail closed but are not a substitute for professional research.
- **R3** Live-database verification, linters and type-checkers are not run in
  this workspace; offline tests simulate RLS via the fake client.
- **R4** The frontend's handling of tokens (storage, expiry) is out of scope
  here; use standard JWT hygiene (short expiry, secure storage, revocation via
  Supabase).

## Control owners

- Backend authorization: `authz/*`, `app.py`, `sessions/*`, `agents/*`.
- Database: `supabase/migrations/*`.
- Offline verification: `tests/*`, `scripts/verify_schema.py`.
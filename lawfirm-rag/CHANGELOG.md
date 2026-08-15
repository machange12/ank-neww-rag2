# Changelog

This changelog documents the security/foundation hardening work performed on the
`lawfirm-rag` project. Historical per-change notes live in
[`CHANGES.md`](./CHANGES.md); the operational SQL applied earlier remains
available in `sql/` (now deprecated in favour of `supabase/migrations`).

All dates are 2026.

## [Unreleased] — Foundation integrity hardening

### WP2 — Server-side authorization and audit integration (available)

- **User-client chat/history/session reads.** `sessions/manager.py` builds chat
  sessions and lists a user's sessions through the caller's own PostgREST client
  (no service-role key). Ownership is enforced by the database
  (`chat_sessions_select_own` / `chat_memory_select_own`), never by parsing the
  session key. New sessions use a generated UUID key; the legacy
  `<user_id>__<client_session_id>` derived key is used only as a documented
  fallback when the row cannot be written.
- **`sessions/supabase_history.py`** now stamps `user_id` and `tenant_id` on every
  chat-memory insert; `agents/rag_agent.py` no longer constructs a service client.
- **Server-side upload classification.** `POST /documents/upload`,
  `POST /documents/ingest-folder`, `POST /documents/ingest-file` compute the
  effective `access_level`/`matter_id` from the caller's DB profile, DB grants and
  a deterministic sensitivity floor over extracted text
  (`authz.policy.classify_upload`). Client-supplied `access_level` is a hint only
  and is never an authorization fact. Matter targets must pass the
  `auth_can_administer_matter_ref` RPC.
- **Admin management.** `GET/POST /admin/users` require an explicit DB admin flag
  (`authz.service.assert_admin`), not a client-supplied role.
- **Audit trail.** `/feedback`, uploads, ingest, chat and the central HTTP error
  handler write security events (denials, rate limits, classifications) through
  `audit/events.py` (service-role, audit path only).
- **CORS.** Production refuses a wildcard allow-origin; development keeps it.

### WP4 — Legal evidence corpus model (available at package level)

- New `corpus/legal_evidence/` package: typed `LegalSource` /
  `LegalDocument` / `LegalDocumentVersion` / `LegalPassage` models; conservative
  status tooling (`is_currently_operational`, `effective_status`,
  `persistable_operational_status`); as-of-date version resolution, immutable
  stable pinpoints, primary-law authority tiers; fictional seed fixtures.
- `supabase/migrations/20260727000002_legal_evidence.sql` provisions the corpus
  tables. `corpus/` never fetches external law.

### WP5 — Citation verification (available at package level)

- New `citations/` package: deterministic text normalization and a conservative
  verifier (`verified` / `weak` / `conflicting` / `unavailable`) that is
  reproducible and fails closed. Reviewer overrides never mutate the original.

### Quality gates

- Offline pytest suite (`tests/`, 64 tests) covering authz policy, upload
  acceptance, citations, legal evidence, schema manifest and static
  service-role-free checks. `scripts/verify_schema.py --manifest-only`,
  `python -m compileall` and the frontend `vite build` all pass.

## Backlog / not enabled

- Legacy `sql/*` files are frozen for reference only; new schema work must go
  through `supabase/migrations/` (see `docs/operations/migrations-and-rollbacks.md`
  and `sql/DEPRECATED.md`).
- Live-DB verification, linters and type-checkers are not wired into this
  workspace (see the final hardening report).
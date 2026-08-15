# Foundation Integrity (WP2) — Design Notes

How the backend guarantees that authorization decisions are made from
authoritative facts and that classification of uploaded material is decided
server-side. This document records the design; ADR-0002 covers the rationale.

## Principles

1. **Authorization facts come from the database.** `user_profiles`
   (`role`, `access_level`, `firm_wide`, `admin`) and `matter_access`
   (`matter_id`, `access_level`, `can_administer`) are the only sources of
   truth. JWT claims (`role`, `access_level`, `matter_ids` in
   `app_metadata`/`user_metadata`) are **hints only** and are never used to
   gate access.
2. **User-facing reads use the caller's own client.** Chat, history, session
   listing and feedback ownership checks go through the user's PostgREST
   client, so RLS (`chat_sessions_select_own`, `chat_memory_select_own`,
   `user_profiles_select_own`) scopes rows to `auth.uid()`. The service-role
   key is confined to `ingest/*`, `cleanup/`, the audit write path
   (`audit/events.py`) and `authz.service.admin_client()` (admin management).
3. **Classification is computed, not trusted.** The client-supplied
   `access_level` is a hint; the effective level is:
   `final = min(ceiling, floor)` where
   - `ceiling` = caller authority (profile level, capped by the matter admin
     level when matter-scoped and not firm-wide);
   - `floor`   = deterministic sensitivity floor from content markers
     (`authz.policy.sensitivity_floor`), default 1.
   A sensitive file can never be stamped below its floor, and a document can
   never be stamped above the caller's ceiling. Classification results and the
   requested (hint) level are recorded in the audit trail.
4. **Matter targets must be administered.** For matter-scoped uploads/ingest,
   the requested matter ref must pass the `auth_can_administer_matter_ref` RPC
   (DB-backed). Firm-pool writes require `firm_wide = true`. The RPC verdict is
   threaded into `classify_upload(... administered=...)` so the pure policy
   function also fails closed when a caller administers a *different* matter.
5. **Explicit admin flag.** `/admin/users` requires `user_profiles.admin =
   true` (via `authz.service.assert_admin`); a high `access_level` is never
   administration.

## Ownership model

- `chat_sessions.user_id_uuid` is the authoritative ownership uuid (RLS key)
  and is set to `auth.uid()` at insert. `user_id` (text) and `tenant_id`
  remain for legacy/partitioning compatibility.
- New sessions use a generated UUID external key. The legacy
  `<user_id>__<session_id>` derived key is **not** parsed for ownership; it is
  used only as a documented fallback if the insert fails (pre-0003 schema).
- Titles for `/sessions` come from the oldest human message in `chat_memory`
  per session, read via the user client.

## Audit

`audit/events.py` appends to `audit_security_events` (immutable, append-only
policies): denied 401/403, rate-limited 429, feedback writes, upload/ingest
classifications. The write path is the one user-triggered flow that may use the
service-role key; it never reads user content.

## CORS

`config.cors_allow_origins` rejects `"*"` in production and requires an explicit
allow-list; development defaults keep the local Vite origins.
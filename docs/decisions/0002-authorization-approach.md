# ADR-0002 — Database-Backed Authorization, User Clients, Server-Side Classification

**Status:** Accepted (2026)

**Context.** The original system trusted JWT `app_metadata` (role, access_level,
matter_ids) and, in places, used the service-role key for chat reads. That lets a
user self-declare privileges and bypasses RLS.

**Decision.**

1. **Authorization facts live in the database.** `user_profiles` (role,
   access_level, firm_wide, admin) and `matter_access` (matter_id, access_level,
   can_administer) are the only authority. JWT claims are hints only.
2. **User-facing reads use the caller's own client** under RLS
   (`chat_sessions_select_own`, `chat_memory_select_own`,
   `user_profiles_select_own`); ownership is DB-enforced, never key-parsed. The
   service-role key is confined to `ingest/*`, `cleanup/`, `audit/events.py` and
   `authz.service.admin_client()`.
3. **Upload/ingest classification is server-side.**
   `authz.policy.classify_upload` computes `min(ceiling, floor)` from DB facts
   and a deterministic sensitivity floor; the client-supplied level is a hint.
   Matter targets must pass the `auth_can_administer_matter_ref` RPC; the verdict
   is threaded into `classify_upload(... administered=...)`.
4. **Explicit admin flag.** `/admin/users` requires `user_profiles.admin = true`.

**Consequences.** Stronger isolation (T1–T6 in the threat model), auditable
decisions, and a clean test seam: the pure policy functions plus an
RLS-simulating fake client give offline coverage of the ownership semantics.

**Alternatives considered.** Service-role reads (rejected: bypasses RLS);
client-supplied authorization facts (rejected: trivially spoofed); policy
evaluated in Postgres only (kept for retrieval via RLS, but the backend still
needs to make classification/ingest decisions, hence the Python policy layer).
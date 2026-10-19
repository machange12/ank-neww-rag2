# Changelog

This changelog documents the security/foundation hardening work performed on the
`lawfirm-rag` project. Historical per-change notes live in
[`CHANGES.md`](./CHANGES.md); the operational SQL applied earlier remains
available in `sql/` (now deprecated in favour of `supabase/migrations`).

All dates are 2026.

## [2026-10-19] — Document privacy, citation provenance, library import

### Per-account document privacy (migration `20261013000016`, ADR-0007)

- `documents` / `document_metadata` gain `owner_user_id` and `doc_scope`
  (`corpus` | `private`), with a fail-closed
  `CHECK (doc_scope <> 'private' or owner_user_id is not null)`. Existing
  `upload_<uuid>_<filename>` rows are backfilled to private.
- `upsert_file()` now requires the caller to state ownership: the upload
  endpoint stamps the authenticated user with `doc_scope="private"`; Drive
  ingest, the polling worker and the Drive webhook stay `corpus`.
- SELECT policies **and both retrieval RPCs** (`match_documents_rls`,
  `hybrid_search_rls`) admit a private row only for its owner or a named
  `document_shares` recipient. Admin and high access levels confer nothing.
- `document_shares` plus SECURITY DEFINER `auth_owns_document`,
  `auth_document_shared_with_me`, `share_document_by_email`,
  `unshare_document`, `list_document_shares`. Shares are owner-only, same-firm,
  and cannot exceed the document's level or matter scope.
- Shared drafts no longer leak a private source: `drafting/store.py` redacts any
  source the reader cannot see (title/page/quote withheld, citation number kept)
  in live sections and version snapshots, failing closed.
- Endpoints: `POST /documents/share`, `DELETE /documents/share`,
  `GET /documents/shares`. UI: Private/Shared badges + share/revoke control.

### Real page and paragraph citations (ADR-0008)

- `ingest/extract.py` returns page/paragraph segments; `ingest/embed.py` maps
  chunks onto them through character offsets and records
  `page_start`/`page_end` or `paragraph_start`/`paragraph_end` plus
  `locator_kind` (`CHUNKING_VERSION …-v3`). Chunks that span a boundary report a
  range instead of guessing.
- `agents/rag_agent.py` aggregates all retrieved locations per document and adds
  a `label` (`p. 12` / `pp. 12–13` / `¶ 4`) and a verbatim ~320-char `quote` to
  each source; DOCX endnotes and the in-app viewer use the same locator. The
  answer keeps plain `[n]`, and the prompt forbids inventing a page.
- `scripts/reingest_provenance.py` backfills Drive-sourced documents and strips
  the synthetic legacy `page_number` from uploads (original bytes are not
  retained, so those files need re-uploading for pages).

### Drafting-library importer (`scripts/import_drafting_library.py`)

- Scaffolds an editable manifest from a folder of the firm's own DOCX/PDF/TXT/MD,
  then seeds templates/clauses/playbooks idempotently; `--dry-run` writes the
  assembled JSON, `--ingest` indexes each source document and records
  `source_file_id` on the clause. Documented in
  `drafting/library_sources/README.md`.

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
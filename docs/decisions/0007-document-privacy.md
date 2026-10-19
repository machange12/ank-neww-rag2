# ADR-0007 — Per-Account Document Privacy

**Status:** Accepted (2026)

**Context.** Visibility was a pure two-dimensional test: a document was readable
if the caller's `access_level` ceiling covered it *and* (`matter_id = ''` or the
caller held a grant for that matter). Nothing recorded *who* uploaded a file. The
uploader's identity survived only as a string prefix inside
`file_id = "upload_<uuid>_<filename>"`, which no policy read, so every user at a
sufficient level could read every other user's upload — including a level-5
partner or an admin reading a junior's draft notes. The requirement is the
opposite: an upload belongs to one account, full stop.

**Decision.**

1. **Two scopes, one explicit column.** `documents.doc_scope` (and the mirror on
   `document_metadata`) is `'corpus'` or `'private'`. `owner_user_id` is the
   uploading account for private rows and normally `NULL` for corpus rows. The
   scope is an explicit discriminator rather than "owner is NULL means shared",
   so that a write path which forgets to stamp an owner cannot silently widen
   access. A `CHECK` makes `doc_scope = 'private'` without an owner impossible,
   so the private branch can never fail open.
2. **Corpus behaviour is unchanged.** Drive ingest, the polling worker and the
   Drive webhook write `doc_scope = 'corpus'` and are governed by exactly the
   pre-existing `access_level` + matter rules.
3. **Uploads are private to one account.** `upsert_file()` gained explicit
   `owner_user_id` / `doc_scope` parameters (no defaults that could guess); the
   upload endpoint passes the authenticated user's id with
   `doc_scope = "private"`. Writes go through the service role, which bypasses
   RLS, so ownership *must* be recorded at write time — no policy can infer it
   later.
4. **Absolute, not role-based.** The private branch of the SELECT policy and of
   both retrieval RPCs (`match_documents_rls`, `hybrid_search_rls`) admits the
   owner and the accounts the owner explicitly named — nobody else. Admin and
   high `access_level` grant nothing here: a level-5 account that is not the
   owner and is not named sees zero rows, and the same predicate is applied
   inside the retrieval functions so a cited passage cannot be retrieved either.
5. **Sharing is explicit, narrow and non-escalating.**
   `public.document_shares(file_id, shared_with, created_by)` records named
   recipients. `share_document_by_email()` (SECURITY DEFINER) re-derives the
   caller from `auth.uid()`, refuses unless the caller owns the file, and refuses
   a recipient whose `access_level` is below the document's level or who has no
   grant for its matter. The `document_shares` INSERT policy additionally calls
   `auth_owns_document(file_id)`, without which any user could insert a share row
   naming themselves for somebody else's file and be handed the document by the
   SELECT policy.
6. **A shared draft does not launder a private source.** Draft sections persist
   the sources they were generated from. On read, `drafting/store.py` resolves
   which of those `file_id`s the *reader* can see and replaces everything else
   with `Restricted source — not shared with you`, keeping only the citation
   number. It fails closed: if that lookup errors, every listed file is treated
   as unreadable. The same redaction is applied to draft version snapshots.

**Consequences.** Access levels and matter grants keep their meaning for the
firm corpus and remain a second gate on private documents (a share cannot hand a
level-3 file to a level-1 account). Uploads are no longer reachable by any
colleague who is not named, so a matter team loses implicit visibility of a
colleague's upload until it is shared or ingested into the corpus — this is the
intended trade-off. Uploads ingested before this change are backfilled from the
`upload_<uuid>_<filename>` prefix. The honest cost is that the original bytes of
a UI upload are not stored, so its page/paragraph provenance cannot be recovered:
citations from those files carry no page number until the file is re-uploaded
(see ADR-0005 and `scripts/reingest_provenance.py`). Corpus documents remain the
place for content that is meant to be firm-wide.

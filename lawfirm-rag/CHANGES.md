# newFirmRAG — Applied Changes Log

### Change 1 — Fix access_level bug (SECURITY)
- Date: 2026-07-27T19:50:42+03:00
- Files touched:
  - ingest\downloader.py
  - ingest\store.py
  - ingest\drive_webhook.py
- Problem fixed: Drive file per-file properties (access_level, matter_id) were not read from the Drive API. This caused documents to be ingested with incorrect/default access metadata, creating a security classification gap.
- Fix applied: list_folder_files() now requests Drive `properties` in the API fields. The ingest flow (both folder re-ingest and webhook) now reads `props = f.get("properties") or {}` and extracts per-file `access_level` and `matter_id` (with sensible fallbacks) and passes them into upsert_file(). upsert_file() metadata now includes access_level and matter_id for every stored chunk so RLS and downstream filters can enforce correct access.

### Change 2 — Better text splitting
- Date: 2026-07-27T19:51:05+03:00
- Files touched:
  - ingest\embed.py
- Problem fixed: CharacterTextSplitter yielded suboptimal chunks for legal text, harming retrieval quality.
- Fix applied: Replaced CharacterTextSplitter with RecursiveCharacterTextSplitter using separators and length_function=len, preserving the configured chunk_size and chunk_overlap.

### Change 3 — Skip re-embedding unchanged files
- Date: 2026-07-27T19:51:28+03:00
- Files touched:
  - ingest\store.py
- Problem fixed: Re-ingesting unchanged files always re-embedded and rewrote vectors, wasting compute and quota.
- Fix applied: upsert_file() now computes a SHA-256 content_hash of the raw bytes and queries document_metadata for an existing content_hash. If unchanged, the function logs and returns early with {"file_id": ..., "chunks": 0, "skipped": True}. The content_hash is also upserted into document_metadata for future comparisons. NOTE: run the following SQL in Supabase to add the column:

  ALTER TABLE public.document_metadata
  ADD COLUMN IF NOT EXISTS content_hash text;

### Change 4 — Hybrid search (vector + keyword)
- Date: 2026-07-27T19:51:51+03:00
- Files touched:
  - search\hybrid.py (new)
  - agents\rag_agent.py
- Problem fixed: Vector-only search can miss keyword signals important in legal search (e.g., statute names, party names). A hybrid approach combining BM25 and vector similarity improves recall and relevance.
- Fix applied: Added search\hybrid.py with hybrid_search(client, query_text, ...) which embeds the query and calls a Supabase RPC named `hybrid_search_rls` (query_text, query_embedding, match_count=20, rrf_k=60). It returns a list of LangChain Document objects and gracefully returns an empty list if the RPC isn't present yet. In agents\rag_agent.py a HybridRetriever (BaseRetriever) was implemented that calls hybrid_search and falls back to vector similarity when hybrid returns nothing. The base retriever in _make_retriever() is replaced with HybridRetriever. NOTE: create the `hybrid_search_rls` RPC in Supabase to fuse pgvector cosine similarity and tsvector BM25 (e.g. via Reciprocal Rank Fusion) for best results.

### Change 5 — Query rewriting
- Date: 2026-07-27T19:52:15+03:00
- Files touched:
  - agents\rag_agent.py
- Problem fixed: Single-query retrieval can miss alternate phrasings. Rewriting/expanding queries often improves recall.
- Fix applied: After constructing the HybridRetriever, it is wrapped with MultiQueryRetriever.from_llm(..., include_original=True). _make_retriever() now accepts an `llm` parameter; run_chat() was updated to pass its llm into _make_retriever(). Cohere rerank (if configured) now wraps the multi-query retriever.

### Change 6 — LangSmith tracing
- Date: 2026-07-27T19:52:39+03:00
- Files touched:
  - config.py
  - app.py
  - .env.example
- Problem fixed: LangChain tracing integration keys/settings were missing.
- Fix applied: Added langchain_tracing_v2, langchain_api_key, and langchain_project to config.py. app.py sets LANGCHAIN_TRACING_V2, LANGCHAIN_API_KEY and LANGCHAIN_PROJECT environment variables at startup when a LangChain API key is present. Added LANGCHAIN_API_KEY and LANGCHAIN_PROJECT to .env.example.

### Change 7 — Streaming endpoint
- Date: 2026-07-27T19:52:58+03:00
- Files touched:
  - agents\rag_agent.py
  - app.py
- Problem fixed: No streaming endpoint existed for real-time token delivery to clients.
- Fix applied: Added an async generator stream_chat() in agents\rag_agent.py that uses ChatOpenAI with streaming=True and yields tokens via astream(). Added a new POST /lawfirm-chat-stream endpoint in app.py that reuses the same auth, RBAC, session and access context logic as /lawfirm-chat-trigger-006 and returns a Server-Sent Events stream (media_type="text/event-stream"). Each token is emitted as `data: {token}\n\n` and the stream ends with `data: [DONE]\n\n`.

### Change 8 — Richer chunk metadata
- Date: 2026-07-27T19:53:22+03:00
- Files touched:
  - ingest\store.py
- Problem fixed: Chunk-level metadata was minimal, making auditing, tracing and RLS enforcement harder.
- Fix applied: upsert_file() now stores richer metadata on every chunk: access_level, matter_id, mime_type, ingested_at (UTC ISO string), chunk_index, and total_chunks. The chunk insertion loop was updated to enumerate chunks so chunk_index and total_chunks are available.

## [CHANGE 9] Source citations in chat answers � 2026-08-01T21:40:00+03:00
- Files: agents/rag_agent.py, app.py, streamlit_app.py
- Problem: answers had no provenance; lawyers won't trust unsourced answers
- Fix: run_chat() returns {answer, sources}; frontend renders clickable source links

## [CHANGE 10] Document upload UI � 2026-08-01T21:40:00+03:00
- Files: app.py, streamlit_app.py
- Problem: ingesting documents required technical API knowledge
- Fix: /documents/upload endpoint + sidebar uploader (partners and above only)

## [CHANGE 11] Document list view � 2026-08-01T21:40:00+03:00
- Files: app.py, streamlit_app.py
- Problem: no visibility into what documents are in the system
- Fix: /documents endpoint + Documents tab showing title, matter, date ingested

## [CHANGE 12] Empty state + error handling � 2026-08-01T21:40:00+03:00
- File: streamlit_app.py
- Problem: raw errors and silent failures looked broken during demos
- Fix: friendly messages for empty results, auth errors, timeouts, connection failures

## [CHANGE 13] Chat history sidebar � 2026-08-01T21:40:00+03:00
- Files: app.py, sessions/manager.py, streamlit_app.py
- Problem: each page load started a new session with no way to return to past chats
- Fix: /sessions endpoint + sidebar listing recent chats; clicking loads that session

## [CHANGE 14] Admin user management UI � 2026-08-01T21:40:00+03:00
- Files: app.py, search/supabase_client.py, streamlit_app.py
- Problem: adding users required direct Supabase dashboard access
- Fix: /admin/users GET+POST endpoints + Admin tab in frontend (access_level 5 only)

## [MVP COMPLETE] Changes 9-14 applied � 2026-08-01T21:40:00+03:00
- Source citations, document upload, document list, error handling,
  chat history, admin user management all implemented
- Frontend: streamlit_app.py updated with tabs, sidebar upload, history
- Backend: 6 new endpoints added to app.py

## [CHANGE 15] Feature batch 3 — streaming memory, hallucination guard, citations, rate limit, intent router, feedback, delta re-ingest, contextual retrieval � 2026-08-13
- Date: 2026-08-13
- Scope: 8 new features across the agent, API and ingest pipeline.

### Feature 1 — Streaming memory persistence
- Files: agents/rag_agent.py
- stream_chat() never persisted the conversation turn. It now buffers every
  yielded token, and once the stream finishes (before the SSE 'sources' event)
  writes the human message + full assembled AI response into the same
  `chat_memory` table run_chat() uses. The write is wrapped in its own
  try/except so a storage failure never breaks the stream.
- IMPLEMENTATION NOTE: the spec called for
  `PostgresChatMessageHistory(connection_string=…)`, but the pinned
  `langchain-postgres==0.0.17` exposes an older API — it requires a live
  `psycopg` connection object and a session_id that is a valid UUID. Our
  session ids are `{user_id}__{session_id}` strings, which would raise
  ValueError there. We instead reuse `SupabaseChatHistory` (already used by
  run_chat(), same table, same `message_to_dict` format read by
  sessions/manager.py), which satisfies "same chat_memory table run_chat() uses".

### Feature 2 — Hallucination guard (no LLM when nothing is retrieved)
- Files: agents/rag_agent.py
- Both run_chat() and stream_chat() now short-circuit when the retriever returns
  zero documents: canned `"I could not find an answer in the firm's document
  repository."` with empty sources, without invoking the LLM. A
  `zero-doc retrieval | session=… | query=…` info log records the event.

### Feature 3 — Chunk-level citations in the API response
- Files: agents/rag_agent.py, app.py
- `_sources_from_docs()` now surfaces `section_heading`, `chunk_index` and
  `page_number` (from each chunk's metadata, defaulting to "" / 0). The
  `ChatResponse.sources` field type widened to `list[dict[str, Any]]` so the
  extra fields pass through. No frontend change required.

### Feature 4 — Per-user rate limiting on chat endpoints
- Files: ratelimit.py (new), config.py, app.py
- New `ratelimit.py` module: in-process `RateLimiter` (dict of user→deque of
  timestamps guarded by asyncio.Lock), sliding-window eviction, raises
  429 with a `Retry-After` header when the limit is hit. Config reads
  `RATE_LIMIT_RPM` (default 20) and `RATE_LIMIT_WINDOW_SECONDS` (default 60).
  Applied in `/lawfirm-chat-trigger-006` and `/lawfirm-chat-stream` right after
  ctx is resolved. The global HTTPException handler now merges `exc.headers`
  into the JSON response so `Retry-After` actually reaches the client.

### Feature 5 — Query intent router
- Files: agents/rag_agent.py
- New `classify_intent()`: one cheap LLM call requesting JSON-only output,
  classifying into factual/comparative/summarization/procedural/out_of_scope
  with a matching retrieve_k (8/15/20/10/0). Parse failures degrade safely to
  factual/8. Both chat functions classify before building the retriever, pass
  `k` into `_make_retriever()`/`HybridRetriever` (which gained an optional `k`
  field overriding settings.retrieve_top_k), and short-circuit with a canned
  out-of-scope response without any retrieval or LLM call when out_of_scope.

### Feature 6 — User feedback endpoint
- Files: app.py, sql/schema.sql
- New `query_feedback` table + indexes (session, user) and `POST /feedback`
  (201). `FeedbackBody` validates rating is 1 or -1 via a pydantic field_validator
  (422 otherwise). user_id comes from the verified JWT (never the body) — RLS /
  auth patterns unchanged. Insert uses psycopg directly via settings.postgres_dsn.

### Feature 7 — Document freshness (delta re-ingest on Drive update)
- Files: ingest/downloader.py, ingest/store.py, ingest/drive_webhook.py,
  ingest/scheduler.py, sql/schema.sql
- `list_folder_files()` now requests `modifiedTime` from Drive.
- New `store.check_last_modified()` compares the Drive modifiedTime against
  `document_metadata.drive_modified_time`; callers skip download+embed entirely
  when unchanged, logging `Skipping unchanged file: <file_id>` and returning
  `{"status": "skipped", "file_id": …}`.
- `upsert_file()` accepts and persists `drive_modified_time`.
- Applied to all three ingest paths: folder re-ingest (`ingest_folder`, which
  now also reports a `skipped` count), Drive webhook, and the 6-hour poll
  scheduler (mod-time fast-path before the existing content-hash check).
- SQL: `alter table document_metadata add column if not exists drive_modified_time text;`
  run in Supabase to add the new column.

### Feature 8 — Contextual chunk enrichment before embedding
- Files: ingest/embed.py, ingest/store.py
- New `enrich_chunk()` prefixes each chunk with
  `Document: <file_title>\nSection: <section_heading|General>\n\n<text>` for
  embedding only. `embed_chunks()` takes `list[TextChunk]` + optional
  `file_title`, enriches internally, and embeds the enriched text; `store.py`
  passes the file title. The stored chunk text stays clean (citations and
  stored vectors' content are unaffected) — only the embedding-model input
  changes.

### Deployment notes
- Run the new schema.sql sections in Supabase (query_feedback table + indexes,
  and the document_metadata drive_modified_time ALTER).
- No new top-level dependencies were added.
- .env.example documents the two new rate-limit settings.

# newFirmRAG â€” Applied Changes Log

### Change 1 â€” Fix access_level bug (SECURITY)
- Date: 2026-07-27T19:50:42+03:00
- Files touched:
  - ingest\downloader.py
  - ingest\store.py
  - ingest\drive_webhook.py
- Problem fixed: Drive file per-file properties (access_level, matter_id) were not read from the Drive API. This caused documents to be ingested with incorrect/default access metadata, creating a security classification gap.
- Fix applied: list_folder_files() now requests Drive `properties` in the API fields. The ingest flow (both folder re-ingest and webhook) now reads `props = f.get("properties") or {}` and extracts per-file `access_level` and `matter_id` (with sensible fallbacks) and passes them into upsert_file(). upsert_file() metadata now includes access_level and matter_id for every stored chunk so RLS and downstream filters can enforce correct access.

### Change 2 â€” Better text splitting
- Date: 2026-07-27T19:51:05+03:00
- Files touched:
  - ingest\embed.py
- Problem fixed: CharacterTextSplitter yielded suboptimal chunks for legal text, harming retrieval quality.
- Fix applied: Replaced CharacterTextSplitter with RecursiveCharacterTextSplitter using separators and length_function=len, preserving the configured chunk_size and chunk_overlap.

### Change 3 â€” Skip re-embedding unchanged files
- Date: 2026-07-27T19:51:28+03:00
- Files touched:
  - ingest\store.py
- Problem fixed: Re-ingesting unchanged files always re-embedded and rewrote vectors, wasting compute and quota.
- Fix applied: upsert_file() now computes a SHA-256 content_hash of the raw bytes and queries document_metadata for an existing content_hash. If unchanged, the function logs and returns early with {"file_id": ..., "chunks": 0, "skipped": True}. The content_hash is also upserted into document_metadata for future comparisons. NOTE: run the following SQL in Supabase to add the column:

  ALTER TABLE public.document_metadata
  ADD COLUMN IF NOT EXISTS content_hash text;

### Change 4 â€” Hybrid search (vector + keyword)
- Date: 2026-07-27T19:51:51+03:00
- Files touched:
  - search\hybrid.py (new)
  - agents\rag_agent.py
- Problem fixed: Vector-only search can miss keyword signals important in legal search (e.g., statute names, party names). A hybrid approach combining BM25 and vector similarity improves recall and relevance.
- Fix applied: Added search\hybrid.py with hybrid_search(client, query_text, ...) which embeds the query and calls a Supabase RPC named `hybrid_search_rls` (query_text, query_embedding, match_count=20, rrf_k=60). It returns a list of LangChain Document objects and gracefully returns an empty list if the RPC isn't present yet. In agents\rag_agent.py a HybridRetriever (BaseRetriever) was implemented that calls hybrid_search and falls back to vector similarity when hybrid returns nothing. The base retriever in _make_retriever() is replaced with HybridRetriever. NOTE: create the `hybrid_search_rls` RPC in Supabase to fuse pgvector cosine similarity and tsvector BM25 (e.g. via Reciprocal Rank Fusion) for best results.

### Change 5 â€” Query rewriting
- Date: 2026-07-27T19:52:15+03:00
- Files touched:
  - agents\rag_agent.py
- Problem fixed: Single-query retrieval can miss alternate phrasings. Rewriting/expanding queries often improves recall.
- Fix applied: After constructing the HybridRetriever, it is wrapped with MultiQueryRetriever.from_llm(..., include_original=True). _make_retriever() now accepts an `llm` parameter; run_chat() was updated to pass its llm into _make_retriever(). Cohere rerank (if configured) now wraps the multi-query retriever.

### Change 6 â€” LangSmith tracing
- Date: 2026-07-27T19:52:39+03:00
- Files touched:
  - config.py
  - app.py
  - .env.example
- Problem fixed: LangChain tracing integration keys/settings were missing.
- Fix applied: Added langchain_tracing_v2, langchain_api_key, and langchain_project to config.py. app.py sets LANGCHAIN_TRACING_V2, LANGCHAIN_API_KEY and LANGCHAIN_PROJECT environment variables at startup when a LangChain API key is present. Added LANGCHAIN_API_KEY and LANGCHAIN_PROJECT to .env.example.

### Change 7 â€” Streaming endpoint
- Date: 2026-07-27T19:52:58+03:00
- Files touched:
  - agents\rag_agent.py
  - app.py
- Problem fixed: No streaming endpoint existed for real-time token delivery to clients.
- Fix applied: Added an async generator stream_chat() in agents\rag_agent.py that uses ChatOpenAI with streaming=True and yields tokens via astream(). Added a new POST /lawfirm-chat-stream endpoint in app.py that reuses the same auth, RBAC, session and access context logic as /lawfirm-chat-trigger-006 and returns a Server-Sent Events stream (media_type="text/event-stream"). Each token is emitted as `data: {token}\n\n` and the stream ends with `data: [DONE]\n\n`.

### Change 8 â€” Richer chunk metadata
- Date: 2026-07-27T19:53:22+03:00
- Files touched:
  - ingest\store.py
- Problem fixed: Chunk-level metadata was minimal, making auditing, tracing and RLS enforcement harder.
- Fix applied: upsert_file() now stores richer metadata on every chunk: access_level, matter_id, mime_type, ingested_at (UTC ISO string), chunk_index, and total_chunks. The chunk insertion loop was updated to enumerate chunks so chunk_index and total_chunks are available.

## [CHANGE 9] Source citations in chat answers — 2026-08-01T21:40:00+03:00
- Files: agents/rag_agent.py, app.py, streamlit_app.py
- Problem: answers had no provenance; lawyers won't trust unsourced answers
- Fix: run_chat() returns {answer, sources}; frontend renders clickable source links

## [CHANGE 10] Document upload UI — 2026-08-01T21:40:00+03:00
- Files: app.py, streamlit_app.py
- Problem: ingesting documents required technical API knowledge
- Fix: /documents/upload endpoint + sidebar uploader (partners and above only)

## [CHANGE 11] Document list view — 2026-08-01T21:40:00+03:00
- Files: app.py, streamlit_app.py
- Problem: no visibility into what documents are in the system
- Fix: /documents endpoint + Documents tab showing title, matter, date ingested

## [CHANGE 12] Empty state + error handling — 2026-08-01T21:40:00+03:00
- File: streamlit_app.py
- Problem: raw errors and silent failures looked broken during demos
- Fix: friendly messages for empty results, auth errors, timeouts, connection failures

## [CHANGE 13] Chat history sidebar — 2026-08-01T21:40:00+03:00
- Files: app.py, sessions/manager.py, streamlit_app.py
- Problem: each page load started a new session with no way to return to past chats
- Fix: /sessions endpoint + sidebar listing recent chats; clicking loads that session

## [CHANGE 14] Admin user management UI — 2026-08-01T21:40:00+03:00
- Files: app.py, search/supabase_client.py, streamlit_app.py
- Problem: adding users required direct Supabase dashboard access
- Fix: /admin/users GET+POST endpoints + Admin tab in frontend (access_level 5 only)

## [MVP COMPLETE] Changes 9-14 applied — 2026-08-01T21:40:00+03:00
- Source citations, document upload, document list, error handling,
  chat history, admin user management all implemented
- Frontend: streamlit_app.py updated with tabs, sidebar upload, history
- Backend: 6 new endpoints added to app.py

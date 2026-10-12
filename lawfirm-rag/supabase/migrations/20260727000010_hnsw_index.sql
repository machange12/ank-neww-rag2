-- Swap ivfflat -> HNSW on documents.embedding.
-- ivfflat with lists=100 is a fixed cell count: recall drops as the corpus
-- grows past ~1000 vectors per cell, and query latency stays flat only
-- because it's scanning fewer cells than it should. HNSW gives better
-- recall/latency curves without the "rebuild INDEX after every N inserts"
-- tuning ivfflat needs.
--
-- Safe: index rebuild only, no data change. If you later change the
-- embedding column type (e.g. to vector(1024) for BGE-M3, migration 0011),
-- this index is dropped by that ALTER and must be recreated at the new dim.

DROP INDEX IF EXISTS documents_embedding_idx;

CREATE INDEX documents_embedding_hnsw
  ON public.documents
  USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);

-- Per-session query-time recall knob. Higher = better recall, slower query.
-- 40 is a reasonable starting point; tune with pgbench once corpus size stabilises.
-- ponytail: session GUC not DB default, so the app's connection sets it or
-- pgvector uses its own default (ef_search=40). Set at the pool level if
-- you want it enforced across every query.

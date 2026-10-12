-- Swap embedding column to 1024-dim for BGE-M3 (BAAI/bge-m3).
-- BGE-M3: MTEB top-tier, 8k context, multilingual, matryoshka-truncatable.
--
-- WARNING: pgvector cannot reinterpret vectors across dims. This migration
-- DROPS all existing embeddings. Full re-ingest required afterwards:
--   1. Apply this migration
--   2. (Optional) Point REMOTE_EMBED_URL at the Kaggle notebook (see
--      docs/operations/kaggle-embed-server.md) to speed up bulk embedding
--   3. Run scripts/reingest_all.py  (or however you re-ingest today)
--
-- Rollback: change providers.HUGGINGFACE_EMBEDDING_MODEL back to
-- nomic-embed-text-v1.5, run the reverse migration (vector(1024) ->
-- vector(768)), re-ingest.

-- Drop the HNSW index first (0010) — ALTER COLUMN TYPE would fail otherwise.
DROP INDEX IF EXISTS documents_embedding_hnsw;
DROP INDEX IF EXISTS documents_embedding_idx;

-- Clear vectors (the ALTER below would set them all to NULL anyway; being
-- explicit prevents the "half-swapped state" if the ALTER partially runs).
UPDATE public.documents SET embedding = NULL;

ALTER TABLE public.documents
  ALTER COLUMN embedding TYPE vector(1024);

-- Recreate HNSW at the new dimension. ef_search stays a session GUC.
CREATE INDEX documents_embedding_hnsw
  ON public.documents
  USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);

-- ponytail: no re-ingest script here. Trigger it from the app side
-- (scripts/reingest_all.py, or DocumentsPanel folder-reupload) — the
-- SQL layer intentionally doesn't know where source files live.

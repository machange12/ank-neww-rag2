-- =============================================================
-- Schema: tables used by the Python RAG pipeline
-- Apply in Supabase SQL editor (in this order with the other sql/*.sql files)
-- =============================================================

create extension if not exists vector;

create table if not exists public.documents (
  id           bigserial primary key,
  content      text,
  metadata     jsonb default '{}'::jsonb,
  embedding    vector(1536),
  access_level int default 1,
  matter_id    text
);
create index if not exists documents_embedding_idx
  on public.documents using ivfflat (embedding vector_cosine_ops);

create table if not exists public.document_metadata (
  id           text primary key,
  file_id      text,
  file_title   text,
  url          text,
  mime_type    text,
  ingested_at  timestamptz default now()
);

create table if not exists public.chat_memory (
  id            bigserial primary key,
  session_id    text not null,
  message       jsonb not null,
  created_at    timestamptz default now()
);
create index if not exists chat_memory_session_idx
  on public.chat_memory (session_id, created_at);

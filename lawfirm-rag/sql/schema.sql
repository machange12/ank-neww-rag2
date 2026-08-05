-- ============================================================
-- schema.sql  —  Run first in Supabase SQL Editor
-- ============================================================

-- Enable pgvector
create extension if not exists vector;

-- Documents (vector store)
create table if not exists public.documents (
  id           bigserial primary key,
  content      text        not null,
  metadata     jsonb       not null default '{}',
  embedding    vector(1536),
  access_level int         not null default 1,
  matter_id    text        not null default ''
);

-- ivfflat cosine index — run AFTER loading data (needs rows to train)
-- create index on public.documents using ivfflat (embedding vector_cosine_ops) with (lists = 100);

-- Document metadata (tracks Drive files)
create table if not exists public.document_metadata (
  id          bigserial primary key,
  file_id     text        not null unique,
  file_title  text,
  url         text,
  mime_type   text,
  ingested_at timestamptz not null default now(),
  content_hash text,
  access_level int not null default 1,
  matter_id text not null default ''
);

-- Chat memory (LangChain PostgresChatMessageHistory)
create table if not exists public.chat_memory (
  id         bigserial primary key,
  session_id text        not null,
  message    jsonb       not null,
  created_at timestamptz not null default now()
);
create index if not exists chat_memory_session_idx on public.chat_memory (session_id);

-- Verify:
-- select table_name from information_schema.tables
--  where table_schema='public' and table_name in ('documents','document_metadata','chat_memory');
-- Expect 3 rows.

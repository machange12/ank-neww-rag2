from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parent.parent


def _parse_csv_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    supabase_jwt_secret: str = ""

    postgres_dsn: str = ""

    openai_api_key: str = ""
    groq_api_key: str = ""
    cohere_api_key: str = ""

    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    google_refresh_token: str = ""

    drive_folder_id: str = "1btpYIgt5X9udhk6ueM8rapf5UqbPyANp"
    drive_webhook_secret: str = ""
    # Shared secret required on worker /ingest/manual (operator-triggered
    # re-ingest). Distinct from drive_webhook_secret since it gates a
    # different, more powerful (arbitrary folder re-ingest) operation.
    ingest_worker_token: str = ""

    # Hard cap on a single uploaded file's size, in bytes. Prevents an
    # unbounded multipart upload from exhausting worker memory.
    max_upload_bytes: int = 25 * 1024 * 1024

    embedding_model: str = "nomic-embed-text"
    # nomic-embed-text-v1.5's native/max output is 768-dim (Matryoshka —
    # truncatable down, never up); the documents.embedding column and both
    # embedder paths (local HF, OpenAI fallback) are all pinned to this value
    # via providers.get_embeddings() so the two paths stay interchangeable.
    # Previously this was 1536 and unused anywhere — the local embedder was
    # silently producing 768-dim vectors against a vector(1536) column, which
    # fails every insert. See supabase/migrations/20260727000007.
    embedding_dim: int = 768
    # llama-3.3-70b-versatile was removed from Groq's lineup entirely
    # (confirmed live: every chat call was 404ing "model does not exist",
    # silently degrading to the hallucination-guard's canned no-answer
    # response since MultiQueryRetriever's LLM call failed too). Verified
    # openai/gpt-oss-120b works and returns clean content (its chain-of-
    # thought goes in a separate `reasoning` field, unlike e.g.
    # qwen/qwen3.6-27b which inlines <think> tags into content and breaks
    # the JSON-extraction in classify_intent).
    chat_model: str = "openai/gpt-oss-120b"
    chat_max_tokens: int = 4096
    # Token-based (tiktoken cl100k_base), not character-based — see
    # ingest/embed.py. nomic-embed-text-v1.5 supports an 8192-token context;
    # the previous character-based 750/200 (~190/~50 tokens) used ~2% of
    # that window and paid a CPU embedding pass per tiny chunk. 900/80 keeps
    # overlap proportionate (~9%) instead of the previous 27%.
    chunk_size: int = 900
    chunk_overlap: int = 80
    retrieve_top_k: int = 20
    context_window: int = 10

    # Optional LLM-boundary pre-pass before the recursive legal-aware
    # splitter (ingest/agentic_chunk.py). Off by default: 2025-26 benchmarks
    # show it beats tuned recursive splitting mainly on structured
    # high-stakes text (which this corpus is), but costs an LLM call per
    # document and needs a golden-set A/B eval on the real corpus before
    # trusting it over the tuned recursive splitter. Always falls back to
    # the recursive splitter on any failure — never blocks ingest.
    use_agentic_chunking: bool = False

    # Per-user sliding-window rate limiting on chat endpoints
    rate_limit_rpm: int = 20
    rate_limit_window_seconds: int = 60

    # Deployment environment. "production" rejects allow_origins=["*"].
    environment: str = "development"

    # Root log level. Nothing in this codebase previously called
    # logging.basicConfig()/dictConfig(), so every logger.info()/.debug() call
    # (retrieval quality logs, ingest/scheduler logs, etc.) was silently
    # dropped — only WARNING+ reached stderr, via Python's unformatted
    # last-resort handler. See logging_setup.configure_logging().
    log_level: str = "INFO"

    # CORS allow-list. In production a strict list is required and "*"
    # is rejected. For local development you may set
    #   CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000"

    @property
    def cors_origins_list(self) -> list[str]:
        return _parse_csv_list(self.cors_origins)

    @property
    def cors_allow_origins(self) -> list[str]:
        origins = self.cors_origins_list
        if self.environment == "production":
            if "*" in origins:
                raise ValueError(
                    "CORS: allow_origins=['*'] is not permitted in production. "
                    "Set CORS_ORIGINS to an explicit allow-list."
                )
            return origins
        return origins or ["*"]

    # Ingest defaults used ONLY by paths without a caller in scope (Drive webhook).
    # User-triggered ingest endpoints MUST pass access_level + matter_id explicitly.
    # These defaults are intentionally conservative — they let the path run but
    # anything ingested under them should be flagged for re-classification.
    default_ingest_access_level: int = 1
    default_ingest_matter_id: str = ""

    # LangSmith / LangChain tracing (optional)
    langchain_tracing_v2: str = "false"
    langchain_api_key: str = ""
    langchain_project: str = "newFirmRAG"


settings = Settings()

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

    embedding_model: str = "nomic-embed-text"
    embedding_dim: int = 1536
    chat_model: str = "llama-3.3-70b-versatile"
    chat_max_tokens: int = 4096
    chunk_size: int = 750
    chunk_overlap: int = 200
    retrieve_top_k: int = 20
    context_window: int = 10

    # Per-user sliding-window rate limiting on chat endpoints
    rate_limit_rpm: int = 20
    rate_limit_window_seconds: int = 60

    # Deployment environment. "production" rejects allow_origins=["*"].
    environment: str = "development"

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

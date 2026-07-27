from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    supabase_jwt_secret: str = ""

    postgres_dsn: str = ""

    openai_api_key: str = ""
    cohere_api_key: str = ""

    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    google_refresh_token: str = ""

    drive_folder_id: str = "1btpYIgt5X9udhk6ueM8rapf5UqbPyANp"

    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536
    chat_model: str = "gpt-4.1-mini"
    chat_max_tokens: int = 4096
    chunk_size: int = 750
    chunk_overlap: int = 200
    retrieve_top_k: int = 20
    context_window: int = 10

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

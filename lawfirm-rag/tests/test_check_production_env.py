"""Offline tests for the production .env gate (scripts/check_production_env.py).

The checker is stdlib-only and never touches the network, so these are pure
function tests over dicts — no fakes or monkeypatching needed.
"""
from __future__ import annotations

from scripts.check_production_env import (
    check_environment,
    is_placeholder,
    parse_env_file,
)


def _valid_env() -> dict[str, str]:
    return {
        "ENVIRONMENT": "production",
        "CORS_ORIGINS": "https://rag.ak.law",
        "SUPABASE_URL": "https://abcdefghijklmnop.supabase.co",
        "SUPABASE_ANON_KEY": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.anon",
        "SUPABASE_SERVICE_ROLE_KEY": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.svc",
        "SUPABASE_JWT_SECRET": "s3cr3t-jwt-signing-value",
        "POSTGRES_DSN": "postgresql://postgres:pw@db.abcdefghijklmnop.supabase.co:5432/postgres",
        "GROQ_API_KEY": "gsk_live_0123456789",
        "REMOTE_EMBED_URL": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "REMOTE_EMBED_KEY": "AIzaSyRealEmbeddingKey",
        "REMOTE_EMBED_MODEL": "gemini-embedding-001",
        "EMBEDDING_DIM": "768",
    }


# ---------------------------------------------------------------- happy path

def test_valid_production_env_has_no_errors():
    errors, _warnings = check_environment(_valid_env())
    assert errors == []


def test_localhost_cors_is_a_warning_not_an_error():
    env = _valid_env()
    env["CORS_ORIGINS"] = "https://rag.ak.law,http://localhost:5173"
    errors, warnings = check_environment(env)
    assert errors == []
    assert any("localhost" in w for w in warnings)


def test_worker_token_required_only_when_asked():
    env = _valid_env()
    assert check_environment(env)[0] == []
    errors, _ = check_environment(env, require_worker_token=True)
    assert any("INGEST_WORKER_TOKEN" in e for e in errors)


# ------------------------------------------------------------------- errors

def test_wildcard_cors_is_an_error():
    env = _valid_env()
    env["CORS_ORIGINS"] = "*"
    errors, _ = check_environment(env)
    assert any("CORS_ORIGINS" in e and "*" in e for e in errors)


def test_placeholder_secret_is_an_error():
    env = _valid_env()
    env["SUPABASE_ANON_KEY"] = "your-supabase-anon-key"
    errors, _ = check_environment(env)
    assert any("SUPABASE_ANON_KEY" in e for e in errors)


def test_missing_embedder_is_an_error():
    # The container ships no torch — without a hosted embedder the first embed
    # call fails, so this must never deploy.
    env = _valid_env()
    for key in ("REMOTE_EMBED_URL", "REMOTE_EMBED_KEY", "REMOTE_EMBED_MODEL"):
        env.pop(key)
    errors, _ = check_environment(env)
    assert any("embedder" in e.lower() for e in errors)


def test_partially_configured_remote_embed_is_an_error():
    env = _valid_env()
    env.pop("REMOTE_EMBED_MODEL")
    errors, _ = check_environment(env)
    assert any("REMOTE_EMBED" in e for e in errors)


def test_openai_key_alone_satisfies_the_embedder_requirement():
    env = _valid_env()
    for key in ("REMOTE_EMBED_URL", "REMOTE_EMBED_KEY", "REMOTE_EMBED_MODEL"):
        env.pop(key)
    env["OPENAI_API_KEY"] = "sk-proj-real-key"
    errors, _ = check_environment(env)
    assert not any("embedder" in e.lower() for e in errors)


def test_embedding_dim_mismatch_is_an_error():
    env = _valid_env()
    env["EMBEDDING_DIM"] = "1536"
    errors, _ = check_environment(env)
    assert any("EMBEDDING_DIM" in e for e in errors)


def test_non_production_environment_is_an_error():
    env = _valid_env()
    env["ENVIRONMENT"] = "development"
    errors, _ = check_environment(env)
    assert any("ENVIRONMENT" in e for e in errors)


# ------------------------------------------------------------------ helpers

def test_is_placeholder():
    assert is_placeholder("")
    assert is_placeholder(None)
    assert is_placeholder("your-groq-api-key")
    assert is_placeholder("choose-a-long-random-operator-token")
    assert is_placeholder("https://rag.example.com")
    assert not is_placeholder("gsk_live_0123456789")
    assert not is_placeholder("https://abcdefghij.supabase.co")


def test_parse_env_file_ignores_comments_blank_and_export_and_quotes():
    text = (
        "# a comment\n"
        "\n"
        "ENVIRONMENT=production\n"
        "export CORS_ORIGINS='https://rag.ak.law'\n"
        'GROQ_API_KEY="gsk_live_x"\n'
        "NO_EQUALS_LINE\n"
    )
    env = parse_env_file(text)
    assert env["ENVIRONMENT"] == "production"
    assert env["CORS_ORIGINS"] == "https://rag.ak.law"
    assert env["GROQ_API_KEY"] == "gsk_live_x"
    assert "NO_EQUALS_LINE" not in env

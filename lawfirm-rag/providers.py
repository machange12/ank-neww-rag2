from __future__ import annotations

from typing import Any

from config import settings

HUGGINGFACE_EMBEDDING_MODEL = "nomic-ai/nomic-embed-text-v1.5"
OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"
OPENAI_CHAT_MODEL = "gpt-4.1-mini"

_embeddings: Any = None


def get_embeddings() -> Any:
    """
    Return a lazily-initialised, module-level singleton embeddings instance.

    Loading the local HuggingFace model (nomic-embed-text-v1.5) is expensive on
    CPU, so it is created once and reused across all requests. Returns the OpenAI
    embedder when OPENAI_API_KEY is set (backward compatible).

    Both paths are pinned to ``settings.embedding_dim`` so the two providers
    are interchangeable against the same ``documents.embedding vector(N)``
    column. Previously neither path set this explicitly: the local model's
    native output (768-dim) didn't match the DB column (vector(1536)), so
    every insert failed. OpenAI's text-embedding-3 models support the same
    ``dimensions`` truncation, so switching providers no longer requires a
    schema change.
    """
    global _embeddings
    if _embeddings is not None:
        return _embeddings

    if settings.openai_api_key:
        from langchain_openai import OpenAIEmbeddings

        model = settings.embedding_model
        if model == "nomic-embed-text":
            model = OPENAI_EMBEDDING_MODEL
        _embeddings = OpenAIEmbeddings(
            model=model,
            openai_api_key=settings.openai_api_key,
            dimensions=settings.embedding_dim,
        )
        return _embeddings

    from langchain_huggingface import HuggingFaceEmbeddings

    _embeddings = HuggingFaceEmbeddings(
        model_name=HUGGINGFACE_EMBEDDING_MODEL,
        model_kwargs={"trust_remote_code": True, "truncate_dim": settings.embedding_dim},
    )
    return _embeddings


def make_embeddings() -> Any:
    """Alias for get_embeddings(); kept for backward compatibility."""
    return get_embeddings()


def make_chat_llm(streaming: bool = False) -> Any:
    """
    Backward-compatible chat LLM factory.

    - If GROQ_API_KEY is set, use Groq (new primary provider).
    - Otherwise fall back to OpenAI so existing deployments keep working.
    """
    if settings.groq_api_key:
        from langchain_groq import ChatGroq

        return ChatGroq(
            model=settings.chat_model,
            temperature=0,
            max_tokens=settings.chat_max_tokens,
            groq_api_key=settings.groq_api_key,
            streaming=streaming,
        )

    from langchain_openai import ChatOpenAI

    model = settings.chat_model
    if model == "llama-3.3-70b-versatile":
        model = OPENAI_CHAT_MODEL
    return ChatOpenAI(
        model=model,
        temperature=0,
        max_tokens=settings.chat_max_tokens,
        openai_api_key=settings.openai_api_key,
        streaming=streaming,
    )

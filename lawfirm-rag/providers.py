from __future__ import annotations

from typing import Any

from config import settings

HUGGINGFACE_EMBEDDING_MODEL = "nomic-ai/nomic-embed-text-v1.5"
OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"
OPENAI_CHAT_MODEL = "gpt-4.1-mini"


def make_embeddings() -> Any:
    """
    Backward-compatible embeddings factory.

    - If OPENAI_API_KEY is set, use OpenAI embeddings (keeps existing setups
      working and matches the vector(1536) column).
    - Otherwise fall back to a free, locally-run HuggingFace embedder
      (nomic-embed-text-v1.5, no API key required).
    """
    if settings.openai_api_key:
        from langchain_openai import OpenAIEmbeddings

        model = settings.embedding_model
        if model == "nomic-embed-text":
            model = OPENAI_EMBEDDING_MODEL
        return OpenAIEmbeddings(model=model, openai_api_key=settings.openai_api_key)

    from langchain_huggingface import HuggingFaceEmbeddings

    return HuggingFaceEmbeddings(
        model_name=HUGGINGFACE_EMBEDDING_MODEL,
        model_kwargs={"trust_remote_code": True},
    )


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

from __future__ import annotations

from functools import lru_cache
from typing import Any

from config import settings

# nomic-ai/nomic-embed-text-v1.5 (768d) — matches vector(768) column.
# BGE-M3 (1024d) swap is pre-staged in migration 20260727000011 but must
# ship ATOMICALLY: apply migration + change this constant + re-ingest all
# docs in one go. Changing this constant alone (without the migration and
# re-ingest) breaks retrieval — 1024-dim query vectors can't cosine-search
# a vector(768) column.
HUGGINGFACE_EMBEDDING_MODEL = "nomic-ai/nomic-embed-text-v1.5"
OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"
OPENAI_CHAT_MODEL = "gpt-4.1-mini"

_embeddings: Any = None



def embed_prefixes() -> tuple[str, str]:
    """Return (query_prefix, document_prefix) for the active local embedder.

    Nomic-v1.5 was trained with ``search_query:`` / ``search_document:``
    task prefixes. Gated behind ``settings.use_embed_prefixes`` — asymmetric
    prefixes (query yes, docs no) hurt legacy corpora more than the
    both-sides win. BGE-M3 and OpenAI want no prefixes ever.
    """
    if settings.openai_api_key:
        return "", ""
    if not settings.use_embed_prefixes:
        return "", ""
    m = HUGGINGFACE_EMBEDDING_MODEL
    if "nomic-embed-text" in m:
        return "search_query: ", "search_document: "
    return "", ""


def get_bulk_embeddings() -> Any:
    """Embedder used for INGESTION. Same instance as the query path — when
    ``REMOTE_EMBED_URL`` is set both go remote, so ingest and query vectors
    always come from the same model."""
    return get_embeddings()


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

    if settings.remote_embed_url:
        # OpenAI-compatible host serving the SAME model as the local path
        # (Fireworks: https://api.fireworks.ai/inference/v1, or the Kaggle
        # notebook) — raw text in, so embed_prefixes() behaves as for local
        # and stored vectors stay valid. Keeps torch out of the process.
        from langchain_openai import OpenAIEmbeddings

        _embeddings = OpenAIEmbeddings(
            model=HUGGINGFACE_EMBEDDING_MODEL,
            openai_api_base=settings.remote_embed_url,
            openai_api_key=settings.remote_embed_key or "unused",
            dimensions=settings.embedding_dim,
            check_embedding_ctx_length=False,
            timeout=15,  # SDK default is 600s; a hung call stalled whole chats
            max_retries=2,
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


# Cached: building the client costs ~1.3s of CPU (httpx/SSL setup) — paid
# per request before, which is tens of seconds on Render's shared free CPU.
@lru_cache(maxsize=2)
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
            timeout=60,
        )

    from langchain_openai import ChatOpenAI

    model = settings.chat_model
    # settings.chat_model is normally a Groq model id (llama-*, or Groq's
    # "org/model" naming like openai/gpt-oss-120b) — meaningless to the
    # OpenAI API. Previously this only special-cased one exact legacy
    # value (llama-3.3-70b-versatile); any other Groq model id configured
    # here would have been sent to OpenAI's API verbatim and 404'd. Real
    # OpenAI chat model ids all start with "gpt-".
    if not model.startswith("gpt-"):
        model = OPENAI_CHAT_MODEL
    return ChatOpenAI(
        model=model,
        temperature=0,
        max_tokens=settings.chat_max_tokens,
        openai_api_key=settings.openai_api_key,
        streaming=streaming,
    )

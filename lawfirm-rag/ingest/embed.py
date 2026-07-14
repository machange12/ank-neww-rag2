from __future__ import annotations

from langchain.text_splitter import CharacterTextSplitter
from langchain_openai import OpenAIEmbeddings

from config import settings


def chunk_text(text: str) -> list[str]:
    splitter = CharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        separator="\n",
    )
    return splitter.split_text(text)


def embed_chunks(chunks: list[str]) -> list[list[float]]:
    embedder = OpenAIEmbeddings(
        model=settings.embedding_model,
        openai_api_key=settings.openai_api_key,
    )
    return embedder.embed_documents(chunks)

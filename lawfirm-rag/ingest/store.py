from __future__ import annotations

import json
import logging
import hashlib
from datetime import datetime, timezone
from typing import Any

from ingest.classifier import classify_document, extract_legal_entities
from ingest.embed import CHUNKING_VERSION, chunk_text_auto, embed_chunks, total_tokens
from ingest.extract import extract_text
from postgrest_utils import response_data
from search.service_client import make_service_client

from config import settings
from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)


async def check_last_modified(file_id: str, drive_modified_time: str | None) -> bool:
    """
    Return True when the Drive ``modifiedTime`` matches the last ingested value.

    Used for delta re-ingest: callers can skip the download + embed pipeline
    entirely when Drive reports the file has not changed since it was ingested.
    A missing ``drive_modified_time`` (or any query error) means "unknown" and
    returns False so the file is ingested to be safe.
    """
    if not drive_modified_time:
        return False
    client = make_service_client()
    try:
        res = client.table("document_metadata").select("drive_modified_time").eq("file_id", file_id).execute()
        data = response_data(res)
        if isinstance(data, list) and data:
            return data[0].get("drive_modified_time") == drive_modified_time
    except Exception:  # noqa: BLE001
        logger.debug("Could not read drive_modified_time for file_id=%s", file_id)
    return False


async def upsert_file(
    file_id: str,
    file_title: str,
    file_url: str,
    mime_type: str,
    raw_bytes: bytes,
    access_level: int,
    matter_id: str,
    drive_modified_time: str = "",
) -> dict[str, Any]:
    """
    Full ingest pipeline for one file:
      1. Extract text
      2. Chunk + embed
      3. Delete old rows for this file_id (idempotent re-ingest)
      4. Insert new document rows
      5. Upsert document_metadata row
    Uses the SERVICE ROLE client — bypasses RLS intentionally for writes.

    SECURITY: ``access_level`` and ``matter_id`` are REQUIRED. They were
    previously defaulted to ``1`` and ``""``, which silently stamped every
    document as lowest-privilege / no-matter — a real access-control gap
    (the matter scope filter became meaningless and access_level ceilings
    could not be enforced for higher-privilege docs). Callers MUST
    compute these values from the requesting user's role or an explicit
    configuration source. There is no safe default.
    """
    if access_level < 1:
        raise ValueError(
            f"access_level must be >= 1 for file_id={file_id}; got {access_level}"
        )
    if matter_id is None:
        raise ValueError(f"matter_id must not be None for file_id={file_id}")
    client = make_service_client()

    # Compute content hash to detect unchanged files and avoid re-embedding.
    content_hash = hashlib.sha256(raw_bytes).hexdigest()
    try:
        res = client.table("document_metadata").select("content_hash").eq("file_id", file_id).execute()
        existing_hash = None
        # Supabase client returns .data in some variants, or raw list in others
        data = response_data(res)
        if isinstance(data, list) and len(data) > 0:
            existing_hash = data[0].get("content_hash")
    except Exception:
        existing_hash = None

    if existing_hash and existing_hash == content_hash:
        logger.info("skipping unchanged file file_id=%s", file_id)
        return {"file_id": file_id, "chunks": 0, "skipped": True}

    text, total_pages = extract_text(raw_bytes, mime_type)

    # Document intelligence — classify and extract entities once per file.
    # A classifier failure must NEVER break ingest: fall back to empty metadata.
    doc_intelligence: dict[str, Any] = {}
    try:
        _llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, api_key=settings.openai_api_key)
        doc_classification = classify_document(text, file_title, _llm)
        legal_entities = extract_legal_entities(
            text, doc_classification.get("doc_type", "unknown"), _llm
        )
        doc_intelligence = {**doc_classification, **legal_entities}
        logger.info(
            "doc_intelligence file_id=%s type=%s",
            file_id,
            doc_classification.get("doc_type"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("doc_intelligence failed file_id=%s: %s", file_id, exc)
        doc_intelligence = {}

    chunking_llm = None
    if settings.use_agentic_chunking:
        from providers import make_chat_llm

        chunking_llm = make_chat_llm()
    chunks, chunking_method = chunk_text_auto(text, llm=chunking_llm)
    if not chunks:
        logger.warning("No chunks produced for file_id=%s", file_id)
        return {"file_id": file_id, "chunks": 0}

    embeddings = embed_chunks(chunks, file_title=file_title)

    # Cutoff captured BEFORE the new rows are inserted: only rows older than
    # this timestamp are stale. Insert-then-delete (instead of the previous
    # delete-then-insert) means an insert failure below leaves the existing
    # searchable rows in place rather than leaving the file with none.
    reingest_cutoff = datetime.now(timezone.utc).isoformat()

    ingested_at = datetime.now(timezone.utc).isoformat()
    total_chunks = len(chunks)
    chunks_per_page = max(1, total_chunks // max(1, total_pages))

    # Insert new document rows (one row per chunk). Include richer metadata per chunk.
    rows = [
        {
            "content": chunk.text,
            "embedding": embedding,
            "metadata": {
                "file_id": file_id,
                "file_title": file_title,
                "url": file_url,
                "access_level": access_level,
                "matter_id": matter_id,
                "mime_type": mime_type,
                "ingested_at": ingested_at,
                "section_heading": chunk.section_heading,
                "chunk_index": chunk.chunk_index,
                "total_chunks": total_chunks,
                "chunking_version": CHUNKING_VERSION,
                "chunking_method": chunking_method,
                "page_number": chunk.chunk_index // chunks_per_page,
                "doc_type": doc_intelligence.get("doc_type", "unknown"),
                "jurisdiction": doc_intelligence.get("jurisdiction", "Unknown"),
                "doc_language": doc_intelligence.get("language", "English"),
                "legal_entities": doc_intelligence,
            },
            "access_level": access_level,
            "matter_id": matter_id,
        }
        for chunk, embedding in zip(chunks, embeddings)
    ]
    client.table("documents").insert(rows).execute()

    # Now that the new rows are safely inserted, delete the file's OLD rows
    # (created before reingest_cutoff). If the insert above had failed, this
    # line never runs and the old, still-searchable rows survive.
    client.rpc(
        "delete_documents_by_file_id_before",
        {"p_file_id": file_id, "p_before": reingest_cutoff},
    ).execute()

    # Upsert metadata record (includes content_hash + drive_modified_time to
    # enable unchanged-file detection)
    client.table("document_metadata").upsert({
        "file_id":     file_id,
        "file_title":  file_title,
        "url":         file_url,
        "mime_type":   mime_type,
        "ingested_at": ingested_at,
        "content_hash": content_hash,
        "drive_modified_time": drive_modified_time,
        "access_level": access_level,
        "matter_id": matter_id,
        "doc_type": doc_intelligence.get("doc_type", "unknown"),
        "legal_entities": json.dumps(doc_intelligence),
    }, on_conflict="file_id").execute()

    token_count = total_tokens(chunks)
    logger.info(
        "Ingested file_id=%s chunks=%d total_tokens=%d avg_tokens_per_chunk=%.0f "
        "chunking_version=%s chunking_method=%s",
        file_id,
        len(chunks),
        token_count,
        token_count / len(chunks),
        CHUNKING_VERSION,
        chunking_method,
    )
    return {"file_id": file_id, "chunks": len(chunks)}

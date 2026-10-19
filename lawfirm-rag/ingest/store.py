from __future__ import annotations

import json
import logging
import hashlib
from datetime import datetime, timezone
from typing import Any

from ingest.classifier import classify_document, extract_legal_entities
from ingest.embed import CHUNKING_VERSION, chunk_text_auto, embed_chunks, total_tokens
from ingest.extract import extract_document
from postgrest_utils import response_data
from search.service_client import make_service_client

from config import settings
from providers import make_chat_llm

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
    force: bool = False,
    owner_user_id: str | None = None,
    doc_scope: str = "corpus",
) -> dict[str, Any]:
    """
    Full ingest pipeline for one file:
      1. Extract text (+ page/paragraph provenance)
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

    SECURITY (ownership): writes go through the service role, which bypasses
    RLS, so the private/shared boundary (migration 20261013000016) can only be
    enforced if the caller states it here.
      * ``doc_scope="corpus"`` (default) — the firm corpus; visible per the
        access_level + matter rules. Correct for Drive ingest, the polling
        worker and the Drive webhook.
      * ``doc_scope="private", owner_user_id=<uuid>`` — one account's upload.
        Correct ONLY for the authenticated upload endpoint, which passes the
        caller's own user id.
    A private row with no owner is rejected (the DB CHECK constraint would
    reject it too) because it would be invisible to everyone, including its
    uploader.

    ``force=True`` bypasses the unchanged-content short-circuit. Needed when
    re-ingesting solely to populate new metadata (e.g. page/paragraph
    provenance), where the bytes have not changed.
    """
    if access_level < 1:
        raise ValueError(
            f"access_level must be >= 1 for file_id={file_id}; got {access_level}"
        )
    if matter_id is None:
        raise ValueError(f"matter_id must not be None for file_id={file_id}")
    if doc_scope not in ("corpus", "private"):
        raise ValueError(f"doc_scope must be 'corpus' or 'private'; got {doc_scope!r}")
    if doc_scope == "private" and not owner_user_id:
        raise ValueError(
            f"doc_scope='private' requires owner_user_id for file_id={file_id}"
        )
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

    if existing_hash and existing_hash == content_hash and not force:
        logger.info("skipping unchanged file file_id=%s", file_id)
        return {"file_id": file_id, "chunks": 0, "skipped": True}

    extracted = extract_document(raw_bytes, mime_type)
    text, total_pages = extracted.text, extracted.total_pages

    # Document intelligence — classify and extract entities once per file.
    # A classifier failure must NEVER break ingest: fall back to empty metadata.
    doc_intelligence: dict[str, Any] = {}
    try:
        # Was hardcoded to ChatOpenAI(api_key=settings.openai_api_key) — this
        # deployment only configures GROQ_API_KEY, so that client had no valid
        # credentials and every classify_document()/extract_legal_entities()
        # call failed auth silently (caught below, logged as a warning),
        # defaulting doc_type to "unknown" for every ingested file. This uses
        # the same provider-selection logic as chat (Groq when configured,
        # OpenAI fallback), so classification actually runs.
        _llm = make_chat_llm()
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
        # No local re-import here: it made make_chat_llm a function-local
        # name, so the doc-intelligence call above raised UnboundLocalError
        # and every upload was stored as doc_type "unknown".
        chunking_llm = make_chat_llm()
    chunks, chunking_method = chunk_text_auto(
        text,
        llm=chunking_llm,
        segments=extracted.segments,
        locator_kind=extracted.locator_kind,
    )
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
                # Real provenance from ingest.extract (not a chunk-count
                # guess). Pages are 1-based PDF pages; paragraphs are 1-based
                # over non-empty paragraphs for DOCX/text. ``page_number`` is
                # kept as a back-compat alias of page_start.
                "page_start": chunk.page_start,
                "page_end": chunk.page_end,
                "page_number": chunk.page_start,
                "paragraph_start": chunk.paragraph_start,
                "paragraph_end": chunk.paragraph_end,
                "locator_kind": chunk.locator_kind,
                "char_start": chunk.char_start,
                "char_end": chunk.char_end,
                "total_pages": total_pages,
                "doc_type": doc_intelligence.get("doc_type", "unknown"),
                "jurisdiction": doc_intelligence.get("jurisdiction", "Unknown"),
                "doc_language": doc_intelligence.get("language", "English"),
                "legal_entities": doc_intelligence,
            },
            "access_level": access_level,
            "matter_id": matter_id,
            "owner_user_id": owner_user_id,
            "doc_scope": doc_scope,
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

    # Cached answers may now be stale (new/changed document).
    from agents.rag_agent import clear_answer_cache
    clear_answer_cache()

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
        "owner_user_id": owner_user_id,
        "doc_scope": doc_scope,
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

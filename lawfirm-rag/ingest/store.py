from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from ingest.embed import chunk_text, embed_chunks
from ingest.extract import extract_text
from search.service_client import make_service_client

logger = logging.getLogger(__name__)


async def upsert_file(
    file_id: str,
    file_title: str,
    file_url: str,
    mime_type: str,
    raw_bytes: bytes,
    access_level: int,
    matter_id: str,
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

    text = extract_text(raw_bytes, mime_type)
    chunks = chunk_text(text)
    if not chunks:
        logger.warning("No chunks produced for file_id=%s", file_id)
        return {"file_id": file_id, "chunks": 0}

    embeddings = embed_chunks(chunks)

    # Delete old vectors for this file (idempotent re-ingest)
    client.rpc("delete_documents_by_file_id", {"p_file_id": file_id}).execute()

    # Insert new document rows
    rows = [
        {
            "content":      chunk,
            "embedding":    embedding,
            "metadata":     {"file_id": file_id, "file_title": file_title, "url": file_url},
            "access_level": access_level,
            "matter_id":    matter_id,
        }
        for chunk, embedding in zip(chunks, embeddings)
    ]
    client.table("documents").insert(rows).execute()

    # Upsert metadata record
    client.table("document_metadata").upsert({
        "file_id":     file_id,
        "file_title":  file_title,
        "url":         file_url,
        "mime_type":   mime_type,
        "ingested_at": datetime.now(timezone.utc).isoformat(),
    }, on_conflict="file_id").execute()

    logger.info("Ingested file_id=%s chunks=%d", file_id, len(chunks))
    return {"file_id": file_id, "chunks": len(chunks)}

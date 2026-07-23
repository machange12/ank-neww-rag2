from __future__ import annotations

import logging
from typing import Any

from config import settings
from ingest.downloader import download_file, list_folder_files
from ingest.store import upsert_file
from search.service_client import make_service_client

logger = logging.getLogger(__name__)


async def handle_drive_event(
    file_id: str,
    event: str,
    access_level: int | None = None,
    matter_id: str | None = None,
) -> dict[str, Any]:
    """
    Handle a single Google Drive push notification.
    Mirrors the n8n File Created / File Updated → Loop Over Items path.

    SECURITY: Drive push notifications carry no caller identity, so there is no
    in-request RBAC context to derive ``access_level`` and ``matter_id`` from.
    Callers that DO have a caller in scope (the chat-app ingest endpoint)
    MUST pass these explicitly; for unauthenticated Drive pushes we fall back
    to conservative ``config.default_ingest_access_level`` /
    ``config.default_ingest_matter_id`` and LOG A WARNING so mis-classification
    is visible. Long-term, the proper fix is per-folder/drive metadata
    (handled in the Item 5 upgrade) — re-classify anything ingested via this
    fallback path.
    """
    if event in ("remove", "trash"):
        client = make_service_client()
        client.rpc("delete_documents_by_file_id", {"p_file_id": file_id}).execute()
        client.table("document_metadata").delete().eq("file_id", file_id).execute()
        logger.info("Deleted vectors for removed file_id=%s", file_id)
        return {"status": "deleted", "file_id": file_id}

    if access_level is None:
        access_level = settings.default_ingest_access_level
        logger.warning(
            "Drive ingest for file_id=%s: no caller-provided access_level, "
            "falling back to default=%d. Re-classify if this document requires "
            "a higher clearance level.",
            file_id,
            access_level,
        )
    if matter_id is None:
        matter_id = settings.default_ingest_matter_id
        logger.warning(
            "Drive ingest for file_id=%s: no caller-provided matter_id, "
            'falling back to default="%s". Re-classify if this document belongs '
            "to a specific matter.",
            file_id,
            matter_id,
        )

    files = list_folder_files()
    file_meta = next((f for f in files if f["id"] == file_id), None)
    if not file_meta:
        logger.warning("file_id=%s not found in Drive folder — skipping", file_id)
        return {"status": "not_found", "file_id": file_id}

    raw = download_file(file_id, file_meta["mimeType"])
    result = await upsert_file(
        file_id=file_id,
        file_title=file_meta["name"],
        file_url=file_meta.get("webViewLink", ""),
        mime_type=file_meta["mimeType"],
        raw_bytes=raw,
        access_level=access_level,
        matter_id=matter_id,
    )
    return {"status": "ingested", **result}

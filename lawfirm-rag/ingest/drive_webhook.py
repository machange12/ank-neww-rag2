from __future__ import annotations

import logging
from typing import Any

from ingest.downloader import download_file, list_folder_files
from ingest.store import upsert_file
from search.service_client import make_service_client

logger = logging.getLogger(__name__)


async def handle_drive_event(file_id: str, event: str) -> dict[str, Any]:
    """
    Handle a single Google Drive push notification.
    Mirrors the n8n File Created / File Updated → Loop Over Items path.
    """
    if event in ("remove", "trash"):
        # Delete vectors for this file
        client = make_service_client()
        client.rpc("delete_documents_by_file_id", {"p_file_id": file_id}).execute()
        client.table("document_metadata").delete().eq("file_id", file_id).execute()
        logger.info("Deleted vectors for removed file_id=%s", file_id)
        return {"status": "deleted", "file_id": file_id}

    # For add / update: find the file in the folder list to get its metadata
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
    )
    return {"status": "ingested", **result}

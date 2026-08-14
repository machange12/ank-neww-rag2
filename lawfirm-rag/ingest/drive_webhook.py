from __future__ import annotations

import hmac
import logging
from typing import Any

from fastapi import HTTPException

from config import settings
from ingest.downloader import download_file, list_folder_files
from ingest.store import check_last_modified, upsert_file
from search.service_client import make_service_client

logger = logging.getLogger(__name__)


def verify_drive_webhook(request_headers: dict, settings) -> None:
    """Verify Google Drive push notifications using X-Goog-Channel-Token."""
    token = (
        request_headers.get("X-Goog-Channel-Token")
        or request_headers.get("x-goog-channel-token")
        or ""
    )
    expected = settings.drive_webhook_secret or ""
    if not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=403, detail="Invalid Google Drive webhook token")


async def handle_drive_event(
    file_id: str,
    event: str,
    access_level: int | None = None,
    matter_id: str | None = None,
    request_headers: dict | None = None,
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
    if request_headers is not None:
        verify_drive_webhook(request_headers, settings)

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

    # Delta re-ingest: skip when the Drive modifiedTime is unchanged.
    drive_modified_time = file_meta.get("modifiedTime", "")
    if await check_last_modified(file_id, drive_modified_time):
        logger.info("Skipping unchanged file: %s", file_id)
        return {"status": "skipped", "file_id": file_id}

    raw = download_file(file_id, file_meta["mimeType"])

    # Prefer per-file Drive properties when present; fall back to provided args
    props = file_meta.get("properties") or {}
    try:
        file_access_level = int(props.get("access_level", access_level or settings.default_ingest_access_level))
    except Exception:
        file_access_level = int(access_level or settings.default_ingest_access_level)
    file_matter_id = props.get("matter_id", matter_id or settings.default_ingest_matter_id)

    result = await upsert_file(
        file_id=file_id,
        file_title=file_meta["name"],
        file_url=file_meta.get("webViewLink", ""),
        mime_type=file_meta["mimeType"],
        raw_bytes=raw,
        access_level=file_access_level,
        matter_id=file_matter_id,
        drive_modified_time=drive_modified_time,
    )
    return {"status": "ingested", **result}

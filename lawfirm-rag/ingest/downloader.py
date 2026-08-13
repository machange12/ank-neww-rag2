from __future__ import annotations

import io
import logging
from typing import Any

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from config import settings

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

GOOGLE_DOC_MIME   = "application/vnd.google-apps.document"
EXPORT_MIME       = "text/plain"


def _drive_service() -> Any:
    creds = Credentials(
        token=None,
        refresh_token=settings.google_refresh_token,
        client_id=settings.google_oauth_client_id,
        client_secret=settings.google_oauth_client_secret,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def list_folder_files() -> list[dict[str, str]]:
    """Return all files in the configured Drive folder."""
    service = _drive_service()
    query = f"'{settings.drive_folder_id}' in parents and trashed=false"
    files: list[dict] = []
    page_token = None
    while True:
        resp = service.files().list(
            q=query,
<<<<<<< HEAD
            fields="nextPageToken, files(id, name, mimeType, modifiedTime, webViewLink, properties)",
=======
            fields="nextPageToken, files(id, name, mimeType, webViewLink, properties)",
>>>>>>> c7d4b0571d87621b092e195d03135e276042d2fc
            pageToken=page_token,
        ).execute()
        files.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return files


def download_file(file_id: str, mime_type: str) -> bytes:
    """Download a Drive file; exports Google Docs as plain text."""
    service = _drive_service()
    if mime_type == GOOGLE_DOC_MIME:
        req = service.files().export_media(fileId=file_id, mimeType=EXPORT_MIME)
    else:
        req = service.files().get_media(fileId=file_id)

    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, req)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue()


async def ingest_folder(
    access_level: int,
    matter_id: str,
) -> dict[str, Any]:
    """
    Full folder re-ingest: list → download → embed → store.

    ``access_level`` and ``matter_id`` are REQUIRED. They are derived from the
    requesting user's RBAC (max access the caller can read) and the matter
    scope they are acting inside. The caller (chat-app endpoint) is in scope
    and MUST supply these — there is no safe default.
    """
<<<<<<< HEAD
    from ingest.store import check_last_modified, upsert_file
=======
    from ingest.store import upsert_file
>>>>>>> c7d4b0571d87621b092e195d03135e276042d2fc

    files = list_folder_files()
    results = {
        "total": len(files),
        "ok": 0,
<<<<<<< HEAD
        "skipped": 0,
=======
>>>>>>> c7d4b0571d87621b092e195d03135e276042d2fc
        "errors": [],
        "access_level": access_level,
        "matter_id": matter_id,
    }
    for f in files:
        try:
<<<<<<< HEAD
            file_id = f["id"]
            drive_modified_time = f.get("modifiedTime", "")

            # Delta re-ingest: skip files whose Drive modifiedTime is unchanged.
            if await check_last_modified(file_id, drive_modified_time):
                logger.info("Skipping unchanged file: %s", file_id)
                results["skipped"] += 1
                continue

            raw = download_file(file_id, f["mimeType"])
=======
            raw = download_file(f["id"], f["mimeType"])
>>>>>>> c7d4b0571d87621b092e195d03135e276042d2fc
            # Drive file custom properties can carry per-file access info.
            props = f.get("properties") or {}
            try:
                file_access_level = int(props.get("access_level", access_level or 1))
            except Exception:
                file_access_level = int(access_level or 1)
            file_matter_id = props.get("matter_id", matter_id or "")

            await upsert_file(
<<<<<<< HEAD
                file_id=file_id,
=======
                file_id=f["id"],
>>>>>>> c7d4b0571d87621b092e195d03135e276042d2fc
                file_title=f["name"],
                file_url=f.get("webViewLink", ""),
                mime_type=f["mimeType"],
                raw_bytes=raw,
                access_level=file_access_level,
                matter_id=file_matter_id,
<<<<<<< HEAD
                drive_modified_time=drive_modified_time,
=======
>>>>>>> c7d4b0571d87621b092e195d03135e276042d2fc
            )
            results["ok"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.error("Error ingesting %s: %s", f["id"], exc)
            results["errors"].append({"file_id": f["id"], "error": str(exc)})
    return results

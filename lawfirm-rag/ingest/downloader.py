from __future__ import annotations

import io
import logging
from typing import Any

from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

from config import settings

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

GOOGLE_DOC_MIME   = "application/vnd.google-apps.document"
EXPORT_MIME       = "text/plain"


class DriveAuthError(Exception):
    """Raised when the Google Drive credentials are missing or rejected."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(
            message
            or (
                "Google Drive authentication failed. The stored refresh token is "
                "expired or revoked. Re-run: "
                "`python scripts/get_refresh_token.py` and update GOOGLE_REFRESH_TOKEN "
                "in your .env file."
            )
        )


def _drive_service() -> Any:
    if not settings.google_refresh_token:
        raise DriveAuthError(
            "Google Drive is not connected. Set GOOGLE_REFRESH_TOKEN in your .env file."
        )
    try:
        creds = Credentials(
            token=None,
            refresh_token=settings.google_refresh_token,
            client_id=settings.google_oauth_client_id,
            client_secret=settings.google_oauth_client_secret,
            token_uri="https://oauth2.googleapis.com/token",
            scopes=SCOPES,
        )
        return build("drive", "v3", credentials=creds, cache_discovery=False)
    except RefreshError as exc:
        raise DriveAuthError() from exc


def _drive_call(call: Any) -> Any:
    """Execute a Drive API call, translating auth failures into DriveAuthError."""
    try:
        return call.execute()
    except RefreshError as exc:
        raise DriveAuthError() from exc
    except HttpError as exc:
        if exc.resp.status in (401, 403):
            raise DriveAuthError() from exc
        raise


def list_folder_files() -> list[dict[str, str]]:
    """Return all files in the configured Drive folder."""
    service = _drive_service()
    query = f"'{settings.drive_folder_id}' in parents and trashed=false"
    files: list[dict] = []
    page_token = None
    while True:
        resp = _drive_call(service.files().list(
            q=query,
            fields="nextPageToken, files(id, name, mimeType, modifiedTime, webViewLink, properties)",
            pageToken=page_token,
        ))
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
        try:
            _, done = downloader.next_chunk()
        except RefreshError as exc:
            raise DriveAuthError() from exc
        except HttpError as exc:
            if exc.resp.status in (401, 403):
                raise DriveAuthError() from exc
            raise
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
    from ingest.store import check_last_modified, upsert_file

    files = list_folder_files()
    results = {
        "total": len(files),
        "ok": 0,
        "skipped": 0,
        "errors": [],
        "access_level": access_level,
        "matter_id": matter_id,
    }
    for f in files:
        try:
            file_id = f["id"]
            drive_modified_time = f.get("modifiedTime", "")

            # Delta re-ingest: skip files whose Drive modifiedTime is unchanged.
            if await check_last_modified(file_id, drive_modified_time):
                logger.info("Skipping unchanged file: %s", file_id)
                results["skipped"] += 1
                continue

            raw = download_file(file_id, f["mimeType"])
            # Drive file custom properties can carry per-file access info.
            props = f.get("properties") or {}
            try:
                file_access_level = int(props.get("access_level", access_level or 1))
            except Exception:
                file_access_level = int(access_level or 1)
            file_matter_id = props.get("matter_id", matter_id or "")

            await upsert_file(
                file_id=file_id,
                file_title=f["name"],
                file_url=f.get("webViewLink", ""),
                mime_type=f["mimeType"],
                raw_bytes=raw,
                access_level=file_access_level,
                matter_id=file_matter_id,
                drive_modified_time=drive_modified_time,
            )
            results["ok"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.error("Error ingesting %s: %s", f["id"], exc)
            results["errors"].append({"file_id": f["id"], "error": str(exc)})
    return results

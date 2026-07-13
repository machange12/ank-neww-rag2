from __future__ import annotations

import io

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google.oauth2.credentials import Credentials


def build_drive_service(creds: Credentials):
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def list_folder(service, folder_id: str, fields: str = "nextPageToken, files(id,name,mimeType,webViewLink)"):
    q = f"'{folder_id}' in parents and trashed=false"
    files: list[dict] = []
    page_token = None
    while True:
        resp = (
            service.files()
            .list(q=q, fields=fields, pageToken=page_token, supportsAllDrives=True)
            .execute()
        )
        files.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return files


def list_folder_meta_for_cleanup(service, folder_id: str):
    return list_folder(service, folder_id, fields="nextPageToken, files(id)")


def download_file(service, file_id: str, mime_type: str | None) -> bytes:
    if mime_type == "application/vnd.google-apps.document":
        request = service.files().export_media(fileId=file_id, mimeType="text/plain")
    else:
        request = service.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue()

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, FastAPI, Request

from ingest.store import build_documents, delete_old_rows_for_file, insert_documents, insert_metadata
from search.service_client import make_service_client


router = APIRouter()


def _now_iso() -> str:
    return dt.datetime.utcnow().isoformat() + "Z"


async def process_file(item: dict) -> None:
    from ingest.downloader import build_drive_service, download_file
    from ingest.embed import get_embeddings
    from ingest.extract import extract_pdf, extract_text
    from config import settings
    import base64
    import os

    file_id = item.get("file_id")
    file_title = item.get("file_title", file_id)
    file_url = item.get("file_url", "")
    file_type = item.get("file_type", "")

    if not file_id:
        return

    client = make_service_client()
    delete_old_rows_for_file(client, file_id)

    try:
        from google.oauth2.credentials import Credentials

        creds = Credentials(
            token=None,
            refresh_token=settings.google_refresh_token or os.environ.get("GOOGLE_REFRESH_TOKEN", ""),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=settings.google_oauth_client_id,
            client_secret=settings.google_oauth_client_secret,
            scopes=["https://www.googleapis.com/auth/drive.readonly"],
        )
        service = build_drive_service(creds)
        data = download_file(service, file_id, file_type)
    except Exception:
        data = b""

    if file_type == "application/pdf":
        text = extract_pdf(data)
    else:
        text = extract_text(data)

    metadata = {
        "file_id": file_id,
        "file_title": file_title,
        "url": file_url,
        "ingested_at": _now_iso(),
    }
    insert_metadata(
        client,
        {
            "id": file_id,
            "file_id": file_id,
            "file_title": file_title,
            "url": file_url,
            "mime_type": file_type,
            "ingested_at": _now_iso(),
        },
    )

    docs = build_documents(text, metadata)
    if not docs:
        return
    embed = get_embeddings()
    insert_documents(client, embed, docs, file_id, file_title, file_url)


@router.post("/drive/webhook")
async def drive_webhook(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        body = {}
    items = body.get("items") or body.get("files") or ([body] if body else [])
    for it in items:
        await process_file(it)
    return {"processed": len(items)}


def mount(app: FastAPI) -> None:
    app.include_router(router)

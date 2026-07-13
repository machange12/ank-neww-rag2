from __future__ import annotations

from google.oauth2.credentials import Credentials

from config import settings
from ingest.downloader import build_drive_service, list_folder_meta_for_cleanup
from ingest.store import (
    delete_metadata,
    delete_orphan_documents,
    get_all_documents,
    list_metadata,
)


def _drive_creds() -> Credentials:
    return Credentials(
        token=None,
        refresh_token=settings.google_refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.google_oauth_client_id,
        client_secret=settings.google_oauth_client_secret,
        scopes=["https://www.googleapis.com/auth/drive.readonly"],
    )


def _find_document_orphans() -> list[dict]:
    from search.service_client import make_service_client

    client = make_service_client()
    drive_service = build_drive_service(_drive_creds())
    drive_files = list_folder_meta_for_cleanup(drive_service, settings.drive_folder_id)
    drive_ids = {f["id"] for f in drive_files}

    rows = get_all_documents(client)
    orphans: list[dict] = []
    for r in rows:
        meta = r.get("metadata") or {}
        fid = meta.get("file_id")
        if fid and fid not in drive_ids:
            orphans.append({"id": r.get("id"), "file_id": fid})
    return orphans


def _find_metadata_orphans() -> list[dict]:
    from search.service_client import make_service_client

    client = make_service_client()
    drive_service = build_drive_service(_drive_creds())
    drive_files = list_folder_meta_for_cleanup(drive_service, settings.drive_folder_id)
    drive_ids = {f["id"] for f in drive_files}

    rows = list_metadata(client)
    return [
        {"id": r.get("id"), "file_id": r.get("file_id")}
        for r in rows
        if r.get("id") and r.get("id") not in drive_ids
    ]


async def run_cleanup_job() -> dict:
    from search.service_client import make_service_client

    client = make_service_client()
    doc_orphans = _find_document_orphans()
    delete_orphan_documents(client, [int(o["id"]) for o in doc_orphans if o.get("id") is not None])

    meta_orphans = _find_metadata_orphans()
    delete_metadata(client, [str(o["id"]) for o in meta_orphans if o.get("id") is not None])

    return {"deleted_documents": len(doc_orphans), "deleted_metadata": len(meta_orphans)}


if __name__ == "__main__":
    import asyncio

    print(asyncio.run(run_cleanup_job()))

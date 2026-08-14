from __future__ import annotations

import logging
from typing import Any

from ingest.downloader import DriveAuthError, list_folder_files
from search.service_client import make_service_client

logger = logging.getLogger(__name__)


async def run_cleanup() -> dict[str, Any]:
    """
    Nightly orphan detection — mirrors the n8n Schedule Trigger cleanup branch.
    Removes document rows and metadata rows whose source file no longer exists in Drive.
    """
    logger.info("Starting nightly orphan cleanup...")
    client = make_service_client()

    # Live file IDs in Drive
    try:
        drive_files = list_folder_files()
    except DriveAuthError as exc:
        logger.error(
            "Nightly orphan cleanup aborted: %s",
            exc,
        )
        return {"drive_files": 0, "documents_deleted": 0, "metadata_deleted": 0, "errors": [str(exc)]}
    drive_ids: set[str] = {f["id"] for f in drive_files}

    results: dict[str, Any] = {
        "drive_files": len(drive_ids),
        "documents_deleted": 0,
        "metadata_deleted": 0,
        "errors": [],
    }

    # ── Orphan vectors ────────────────────────────────────────────
    doc_rows = client.table("documents").select("id, metadata").execute().data or []
    for row in doc_rows:
        meta = row.get("metadata") or {}
        file_id = meta.get("file_id")
        if file_id and file_id not in drive_ids:
            try:
                client.table("documents").delete().eq("id", row["id"]).execute()
                results["documents_deleted"] += 1
            except Exception as exc:  # noqa: BLE001
                results["errors"].append({"id": row["id"], "error": str(exc)})

    # ── Orphan metadata ───────────────────────────────────────────
    meta_rows = client.table("document_metadata").select("id, file_id").execute().data or []
    for row in meta_rows:
        if row.get("file_id") and row["file_id"] not in drive_ids:
            try:
                client.table("document_metadata").delete().eq("id", row["id"]).execute()
                results["metadata_deleted"] += 1
            except Exception as exc:  # noqa: BLE001
                results["errors"].append({"id": row["id"], "error": str(exc)})

    logger.info(
        "Cleanup done: %d vectors deleted, %d metadata deleted, %d errors",
        results["documents_deleted"],
        results["metadata_deleted"],
        len(results["errors"]),
    )
    return results

from __future__ import annotations

import logging
from typing import Any

from ingest.downloader import DriveAuthError, list_folder_files
from postgrest_utils import response_data
from search.service_client import make_service_client

logger = logging.getLogger(__name__)

_PAGE_SIZE = 1000


def _select_all(client: Any, table: str, columns: str) -> list[dict[str, Any]]:
    """Read every row of ``table``, paging past PostgREST's default max-rows cap.

    A plain ``.select(...).execute()`` silently truncates at Supabase's
    default max-rows (1000) on large corpora, which previously made cleanup
    miss orphans past the first page. Pages with ``.range()`` until a page
    comes back short.
    """
    rows: list[dict[str, Any]] = []
    start = 0
    while True:
        res = client.table(table).select(columns).range(start, start + _PAGE_SIZE - 1).execute()
        page = response_data(res)
        page = page if isinstance(page, list) else []
        rows.extend(page)
        if len(page) < _PAGE_SIZE:
            break
        start += _PAGE_SIZE
    return rows


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
    doc_rows = _select_all(client, "documents", "id, metadata")
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
    # document_metadata's primary key is file_id (there is no "id" column),
    # so deletes below are keyed on file_id, not a nonexistent numeric id.
    meta_rows = _select_all(client, "document_metadata", "file_id")
    for row in meta_rows:
        file_id = row.get("file_id")
        if file_id and file_id not in drive_ids:
            try:
                client.table("document_metadata").delete().eq("file_id", file_id).execute()
                results["metadata_deleted"] += 1
            except Exception as exc:  # noqa: BLE001
                results["errors"].append({"file_id": file_id, "error": str(exc)})

    logger.info(
        "Cleanup done: %d vectors deleted, %d metadata deleted, %d errors",
        results["documents_deleted"],
        results["metadata_deleted"],
        len(results["errors"]),
    )
    return results

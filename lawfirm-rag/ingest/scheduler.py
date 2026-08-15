from __future__ import annotations

import hashlib
import logging
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import settings
from ingest.downloader import DriveAuthError, download_file, list_folder_files
from ingest.store import check_last_modified, upsert_file
from search.service_client import make_service_client

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


def _response_data(response: Any) -> Any:
    return getattr(response, "data", None) or response


def _existing_hash(client: Any, file_id: str) -> str | None:
    try:
        response = (
            client.table("document_metadata")
            .select("content_hash")
            .eq("file_id", file_id)
            .execute()
        )
        data = _response_data(response)
        if isinstance(data, list) and data:
            return data[0].get("content_hash")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read content_hash for file_id=%s: %s", file_id, exc)
    return None


async def sync_drive_folder() -> dict[str, int]:
    """Poll Drive and re-ingest files whose content hash has changed."""
    client = make_service_client()
    try:
        files = list_folder_files()
    except DriveAuthError as exc:
        logger.error(
            "Scheduled Drive sync aborted: %s",
            exc,
        )
        return {"files_checked": 0, "files_re_ingested": 0, "files_skipped": 0}
    checked = 0
    re_ingested = 0
    skipped = 0

    for file_meta in files:
        checked += 1
        file_id = file_meta["id"]
        try:
            # Fast-path: skip downloads entirely when Drive modifiedTime is unchanged.
            drive_modified_time = file_meta.get("modifiedTime", "")
            if await check_last_modified(file_id, drive_modified_time):
                skipped += 1
                continue

            raw = download_file(file_id, file_meta["mimeType"])
            content_hash = hashlib.sha256(raw).hexdigest()
            existing_hash = _existing_hash(client, file_id)

            if existing_hash == content_hash:
                skipped += 1
                continue

            props = file_meta.get("properties") or {}
            if "access_level" not in props or "matter_id" not in props:
                logger.warning(
                    "Scheduled sync for file_id=%s: Drive file has no custom "
                    "access_level/matter_id properties, falling back to "
                    "default access_level=%d matter_id=%r. Re-classify if this "
                    "document requires a higher clearance level or a specific matter.",
                    file_id,
                    settings.default_ingest_access_level,
                    settings.default_ingest_matter_id,
                )
            try:
                access_level = int(props.get("access_level", settings.default_ingest_access_level))
            except Exception:
                access_level = settings.default_ingest_access_level
            matter_id = props.get("matter_id", settings.default_ingest_matter_id)

            await upsert_file(
                file_id=file_id,
                file_title=file_meta["name"],
                file_url=file_meta.get("webViewLink", ""),
                mime_type=file_meta["mimeType"],
                raw_bytes=raw,
                access_level=access_level,
                matter_id=matter_id,
                drive_modified_time=drive_modified_time,
            )
            re_ingested += 1
        except Exception as exc:  # noqa: BLE001
            logger.error("Scheduled ingest failed for file_id=%s: %s", file_id, exc)

    logger.info(
        "Scheduled Drive sync complete: files_checked=%d files_re_ingested=%d files_skipped=%d",
        checked,
        re_ingested,
        skipped,
    )
    return {
        "files_checked": checked,
        "files_re_ingested": re_ingested,
        "files_skipped": skipped,
    }


def start_scheduler() -> AsyncIOScheduler:
    """Start the six-hour Drive polling scheduler once per process."""
    global _scheduler
    if _scheduler and _scheduler.running:
        return _scheduler

    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(
        sync_drive_folder,
        "interval",
        hours=6,
        id="drive_folder_sync",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _scheduler.start()
    logger.info("Drive folder scheduler started; polling every 6 hours.")
    return _scheduler

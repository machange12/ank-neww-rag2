from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from cleanup.orphan_finder import run_cleanup
from ingest.drive_webhook import handle_drive_event

logger = logging.getLogger(__name__)
app = FastAPI(title="Law Firm Ingest Worker", version="2.3456")
scheduler = AsyncIOScheduler()


@app.on_event("startup")
async def startup() -> None:
    # Nightly cleanup at 02:00 UTC — mirrors the n8n Schedule Trigger
    scheduler.add_job(run_cleanup, "cron", hour=2, minute=0, id="nightly_cleanup")
    scheduler.start()
    logger.info("Ingest worker started. Nightly cleanup scheduled at 02:00 UTC.")


@app.on_event("shutdown")
async def shutdown() -> None:
    scheduler.shutdown(wait=False)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/drive/webhook")
async def drive_webhook(
    request: Request,
    x_goog_resource_state: str | None = Header(default=None),
    x_goog_resource_id: str | None = Header(default=None),
) -> JSONResponse:
    """
    Receives Google Drive push notifications.
    Drive sends a 'sync' ping on subscription creation — ignore it.
    All other states trigger file ingest.
    """
    state = x_goog_resource_state or ""
    resource_id = x_goog_resource_id or ""

    if state == "sync":
        return JSONResponse({"status": "sync_ignored"}, status_code=200)

    try:
        body = await request.json()
    except Exception:
        body = {}

    file_id = body.get("file_id") or resource_id
    if not file_id:
        raise HTTPException(status_code=400, detail="Missing file_id")

    logger.info("Drive event: state=%s file_id=%s", state, file_id)
    result = await handle_drive_event(file_id=file_id, event=state)
    return JSONResponse(result, status_code=200)


@app.post("/ingest/manual")
async def manual_ingest(request: Request) -> JSONResponse:
    """Trigger a full folder re-ingest (mirrors the n8n Manual Trigger)."""
    from ingest.downloader import ingest_folder
    result = await ingest_folder()
    return JSONResponse(result, status_code=200)


@app.exception_handler(HTTPException)
async def http_exc(_request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

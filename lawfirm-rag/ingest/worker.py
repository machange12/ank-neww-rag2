from __future__ import annotations

import hmac
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from config import settings
from logging_setup import configure_logging

configure_logging()  # must run before any module below logs anything

from cleanup.orphan_finder import run_cleanup  # noqa: E402
from ingest.downloader import DriveAuthError  # noqa: E402
from ingest.drive_webhook import handle_drive_event, verify_drive_webhook  # noqa: E402

logger = logging.getLogger(__name__)


def _verify_manual_ingest_token(token: str | None) -> None:
    """Require a shared secret on the manual re-ingest trigger.

    This endpoint has no caller RBAC context (unlike the chat-app ingest
    endpoints) and can trigger a full folder re-ingest, so it must never be
    reachable without proof of operator intent. Fails closed: a missing
    ``INGEST_WORKER_TOKEN`` configuration denies every call rather than
    silently accepting unauthenticated requests.
    """
    expected = settings.ingest_worker_token or ""
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="INGEST_WORKER_TOKEN is not configured; manual ingest is disabled",
        )
    if not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=403, detail="Invalid or missing ingest worker token")
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
    request_headers = dict(request.headers)

    verify_drive_webhook(request_headers, settings)

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
    try:
        result = await handle_drive_event(
            file_id=file_id,
            event=state,
            request_headers=request_headers,
        )
    except DriveAuthError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return JSONResponse(result, status_code=200)


@app.post("/ingest/manual")
async def manual_ingest(
    request: Request,
    x_ingest_worker_token: str | None = Header(default=None),
) -> JSONResponse:
    """
    Trigger a full folder re-ingest (mirrors the n8n Manual Trigger).

    SECURITY: Gated by a shared ``X-Ingest-Worker-Token`` header
    (``_verify_manual_ingest_token``) — this endpoint has no caller RBAC
    context and would otherwise let any reachable caller trigger a full
    folder re-ingest. The operator MUST also supply ``access_level`` and
    ``matter_id`` in the request body, avoiding silently stamping every
    re-ingested document with the lowest privilege / no-matter defaults.
    """
    _verify_manual_ingest_token(x_ingest_worker_token)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}

    access_level = body.get("access_level")
    matter_id = body.get("matter_id")
    if access_level is None or matter_id is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Both 'access_level' (int) and 'matter_id' (str) are required "
                "in the request body. The worker has no caller RBAC context, "
                "so silent defaults would re-introduce the access-control gap."
            ),
        )

    from ingest.downloader import ingest_folder
    try:
        result = await ingest_folder(access_level=int(access_level), matter_id=str(matter_id))
    except DriveAuthError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return JSONResponse(result, status_code=200)


@app.exception_handler(HTTPException)
async def http_exc(_request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

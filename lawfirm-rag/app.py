from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from audit import events as audit_events
from config import settings
from deps import request_user_info
from routers import admin, auth, chat, documents

logger = logging.getLogger(__name__)

# LangSmith / LangChain tracing environment (optional). If an API key is set
# enable the v2 tracing environment so downstream LangChain tooling can pick it up.
if settings.langchain_api_key:
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_API_KEY"] = settings.langchain_api_key
    os.environ["LANGCHAIN_PROJECT"] = settings.langchain_project

app = FastAPI(title="Law Firm Secure RAG", version="2.3456")
STATIC_DIR = Path(__file__).resolve().parent / "static"

# CORS: allow-list from environment. "*" is rejected in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

if (STATIC_DIR / "assets").exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(documents.router)
app.include_router(admin.router)


@app.on_event("startup")
async def startup_event() -> None:
    from ingest.scheduler import start_scheduler

    start_scheduler()


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
async def frontend_index() -> FileResponse:
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Frontend has not been built yet")
    return FileResponse(index_path)


@app.options("/{path:path}")
async def preflight(path: str) -> Response:
    allowed_origin = (
        settings.cors_allow_origins[0]
        if settings.cors_allow_origins and settings.cors_allow_origins[0] != "*"
        else ""
    )
    headers: dict[str, str] = {
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Authorization, Content-Type",
    }
    if allowed_origin:
        headers["Access-Control-Allow-Origin"] = allowed_origin
    return Response(status_code=204, headers=headers)


@app.exception_handler(HTTPException)
async def http_exc(request: Request, exc: HTTPException) -> JSONResponse:
    # Audit log denied / rate-limited requests (best-effort; never breaks the
    # response). Denied auth or authorization -> access_denied; rate limit -> rate_limit.
    if exc.status_code == 429:
        info = request_user_info(request)
        audit_events.log_rate_limit(user_id=info["user_id"], ip_address=info["ip_address"])
    elif exc.status_code in (401, 403):
        info = request_user_info(request)
        audit_events.log_denial(
            action=request.url.path,
            reason=str(exc.detail),
            user_id=info["user_id"],
            actor_email=info["actor_email"],
            ip_address=info["ip_address"],
        )

    response_headers = {**(getattr(exc, "headers", {}) or {})}
    if exc.status_code in (401, 403, 429):
        response_headers["Access-Control-Allow-Origin"] = (
            settings.cors_allow_origins[0] if settings.cors_allow_origins else ""
        )
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail},
        headers=response_headers,
    )


@app.get("/{path:path}", include_in_schema=False)
async def frontend_fallback(path: str) -> FileResponse:
    if path.startswith(("auth/", "lawfirm-chat-trigger-006", "healthz", "docs", "openapi.json", "redoc")):
        raise HTTPException(status_code=404, detail="Not found")
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Frontend has not been built yet")
    return FileResponse(index_path)

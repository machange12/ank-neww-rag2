from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Header, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agents.rag_agent import run_chat, stream_chat
from auth.jwt_validator import AuthError, build_user_ctx, verify_jwt
from rbac.checker import build_rbac_block
from rbac.role_matrix import ROLE_MATRIX
from rls.access_context import set_access_context
from rls.filter_builder import build_rls_filter
from search.supabase_client import make_anon_client, make_service_client, make_user_client
from sessions.manager import build_session, list_user_sessions
from config import settings

# LangSmith / LangChain tracing environment (optional). If an API key is set
# enable the v2 tracing environment so downstream LangChain tooling can pick it up.
if settings.langchain_api_key:
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_API_KEY"] = settings.langchain_api_key
    os.environ["LANGCHAIN_PROJECT"] = settings.langchain_project

app = FastAPI(title="Law Firm Secure RAG", version="2.3456")
STATIC_DIR = Path(__file__).resolve().parent / "static"

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

if (STATIC_DIR / "assets").exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")


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


class LoginBody(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    expires_in: int | None = None
    token_type: str | None = None
    user_id: str | None = None
    role: str | None = None
    access_level: int = 1


class ChatBody(BaseModel):
    chatInput: str | None = None
    input: str | None = None
    query: str | None = None
    message: str | None = None
    sessionId: str | None = None


class ChatResponse(BaseModel):
    answer: str
    output: str | None = None
    sources: list[dict[str, str]] = []
    session_id: str


class DriveFileIngestBody(BaseModel):
    file_id: str
    matter_id: str | None = None


@app.post("/auth/login", response_model=LoginResponse)
async def login(body: LoginBody) -> LoginResponse:
    try:
        client = make_anon_client()
        res = client.auth.sign_in_with_password({"email": body.email, "password": body.password})
        sess = res.session if hasattr(res, "session") else res
        user = res.user if hasattr(res, "user") else None
        user_metadata = (getattr(user, "user_metadata", {}) or {}) if user else {}
        app_metadata = (getattr(user, "app_metadata", {}) or {}) if user else {}
        access = getattr(sess, "access_token", "") or ""
        refresh = getattr(sess, "refresh_token", "") or ""
        return LoginResponse(
            access_token=access,
            refresh_token=refresh,
            expires_in=getattr(sess, "expires_in", None),
            token_type=getattr(sess, "token_type", None),
            user_id=getattr(user, "id", None) if user else None,
            role=app_metadata.get("role") if user else None,
            access_level=int(
                user_metadata.get("access_level")
                or app_metadata.get("access_level")
                or (ROLE_MATRIX.get(app_metadata.get("role") or "", {}) or {}).get("level")
                or 1
            ),
        )
    except Exception as exc:
        message = str(exc).strip()
        if "invalid login credentials" in message.lower() or "invalid credentials" in message.lower():
            raise HTTPException(status_code=401, detail=message) from exc
        raise HTTPException(status_code=500, detail=f"LOGIN_FAILED: {message}") from exc


async def _resolve_user_client(authorization: str | None) -> Any:
    if not authorization:
        raise HTTPException(status_code=401, detail="UNAUTHORIZED: missing Authorization header")
    token = authorization.replace("Bearer ", "").replace("bearer ", "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="UNAUTHORIZED: missing token")
    try:
        client = make_user_client(token)
        client.auth.get_user(token)
        return client, token
    except Exception:
        try:
            anon = make_anon_client()
            res = anon.auth.get_user(token)
            if res and getattr(res, "user", None):
                return make_user_client(token), token
        except Exception as exc:
            raise HTTPException(status_code=401, detail=f"UNAUTHORIZED: {exc}") from exc
        raise HTTPException(status_code=401, detail="UNAUTHORIZED: invalid token")


async def _resolve_headers_and_body(request: Request) -> tuple[dict, dict]:
    raw_headers = dict(request.headers.items())
    try:
        body = await request.json()
    except Exception:
        body = {}
    return raw_headers, body if isinstance(body, dict) else {}


def _auth_ctx_from_header(authorization: str | None) -> tuple[dict, dict]:
    if not authorization:
        raise HTTPException(status_code=401, detail="UNAUTHORIZED: missing Authorization header")
    token = authorization.replace("Bearer ", "").replace("bearer ", "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="UNAUTHORIZED: missing token")
    try:
        payload = verify_jwt(token)
        ctx = build_user_ctx(payload, {}, {})
        rbac = build_rbac_block(ctx, {})
    except AuthError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    return ctx, rbac


def _token_from_header(authorization: str | None) -> str:
    token = (authorization or "").replace("Bearer ", "").replace("bearer ", "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing token")
    return token


def _ctx_rbac_from_token(token: str, body: dict[str, Any] | None = None) -> tuple[dict, dict]:
    try:
        payload = verify_jwt(token)
        ctx = build_user_ctx(payload, {}, body or {})
        rbac = build_rbac_block(ctx, body or {})
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return ctx, rbac


def _rbac_level(ctx: dict[str, Any], rbac: dict[str, Any]) -> int:
    return max(int(ctx.get("access_level") or 1), int((rbac.get("perms") or {}).get("level") or 1))


def _require_ingest(authorization: str | None) -> tuple[dict, dict]:
    ctx, rbac = _auth_ctx_from_header(authorization)
    if not (rbac.get("perms") or {}).get("ingest"):
        raise HTTPException(status_code=403, detail="FORBIDDEN: your role cannot ingest documents")
    return ctx, rbac


@app.post("/lawfirm-chat-trigger-006", response_model=ChatResponse)
async def chat_webhook(
    request: Request,
    authorization: str | None = Header(default=None),
) -> ChatResponse:
    raw_headers, body = await _resolve_headers_and_body(request)

    if authorization:
        token = authorization.replace("Bearer ", "").replace("bearer ", "").strip()
        try:
            payload = verify_jwt(token)
        except AuthError as e:
            raise HTTPException(status_code=401, detail=str(e))
        ctx = build_user_ctx(payload, raw_headers, body)
    else:
        try:
            user_client, token = await _resolve_user_client(body.get("authorization") if isinstance(body, dict) else None)  # type: ignore
        except HTTPException:
            body_inner = body.get("body", {}) if isinstance(body, dict) else {}
            auth_inner = body_inner.get("authorization") if isinstance(body_inner, dict) else None
            if not auth_inner:
                raise HTTPException(status_code=401, detail="UNAUTHORIZED: missing credentials") from None
            user_client, token = await _resolve_user_client(auth_inner)  # type: ignore
        try:
            payload = verify_jwt(token)
        except AuthError as e:
            raise HTTPException(status_code=401, detail=str(e))
        ctx = build_user_ctx(payload, raw_headers, body)

    rbac = build_rbac_block(ctx, body)
    session = build_session(ctx, rbac)
    rls = build_rls_filter(rbac, ctx, body)

    if authorization:
        user_client = make_user_client(token)

    await set_access_context(user_client, ctx, rbac, rls)

    chat_input = rls.get("chat_input") or ""
    if not chat_input:
        raise HTTPException(status_code=400, detail="BAD_REQUEST: missing chat input")

    result = await run_chat(
        session_id=session["session_id"],
        system_prefix=rbac["system_prompt_prefix"],
        chat_input=chat_input,
        user_client=user_client,
    )
    return ChatResponse(
        answer=result["answer"],
        output=result["answer"],
        sources=result["sources"],
        session_id=session["session_id"],
    )


@app.post("/lawfirm-chat-stream")
async def chat_webhook_stream(
    request: Request,
    authorization: str | None = Header(default=None),
) -> StreamingResponse:
    """Streaming chat endpoint that returns Server-Sent Events (SSE).

    Mirrors /lawfirm-chat-trigger-006 auth, RBAC and session logic, then
    streams token events as they arrive from the LLM.
    """
    raw_headers, body = await _resolve_headers_and_body(request)

    if authorization:
        token = authorization.replace("Bearer ", "").replace("bearer ", "").strip()
        try:
            payload = verify_jwt(token)
        except AuthError as e:
            raise HTTPException(status_code=401, detail=str(e))
        ctx = build_user_ctx(payload, raw_headers, body)
    else:
        try:
            user_client, token = await _resolve_user_client(body.get("authorization") if isinstance(body, dict) else None)  # type: ignore
        except HTTPException:
            body_inner = body.get("body", {}) if isinstance(body, dict) else {}
            auth_inner = body_inner.get("authorization") if isinstance(body_inner, dict) else None
            if not auth_inner:
                raise HTTPException(status_code=401, detail="UNAUTHORIZED: missing credentials") from None
            user_client, token = await _resolve_user_client(auth_inner)  # type: ignore
        try:
            payload = verify_jwt(token)
        except AuthError as e:
            raise HTTPException(status_code=401, detail=str(e))
        ctx = build_user_ctx(payload, raw_headers, body)

    rbac = build_rbac_block(ctx, body)
    session = build_session(ctx, rbac)
    rls = build_rls_filter(rbac, ctx, body)

    if authorization:
        user_client = make_user_client(token)

    await set_access_context(user_client, ctx, rbac, rls)

    chat_input = rls.get("chat_input") or ""
    if not chat_input:
        raise HTTPException(status_code=400, detail="BAD_REQUEST: missing chat input")

    async def event_stream():
        # stream_chat yields raw token strings; wrap them as SSE 'data: ...' events
        async for token in stream_chat(
            session_id=session["session_id"],
            system_prefix=rbac["system_prompt_prefix"],
            chat_input=chat_input,
            user_client=user_client,
        ):
            token_text = str(token)
            if token_text.startswith("data: "):
                yield token_text
            else:
                yield f"data: {token_text}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/documents/upload")
async def upload_document(
    file: UploadFile = File(...),
    matter_id: str = "",
    access_level: int = 1,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    token = _token_from_header(authorization)
    ctx, rbac = _ctx_rbac_from_token(token)
    if _rbac_level(ctx, rbac) < 3:
        raise HTTPException(status_code=403, detail="Insufficient permissions to upload")

    suffix = os.path.splitext(file.filename or "")[1]
    contents = await file.read()
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name

    try:
        from ingest.store import upsert_file

        safe_filename = file.filename or "uploaded_document"
        file_id = f"upload_{ctx['user_id']}_{safe_filename}"
        result = await upsert_file(
            file_id=file_id,
            file_title=safe_filename,
            file_url="",
            mime_type=file.content_type or "application/octet-stream",
            raw_bytes=contents,
            access_level=access_level,
            matter_id=matter_id,
        )
        return JSONResponse({"status": "ok", "chunks": result.get("chunks", 0), "file_id": file_id})
    finally:
        os.unlink(tmp_path)


@app.get("/documents")
async def list_documents(
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    token = _token_from_header(authorization)
    _ctx_rbac_from_token(token)
    user_client = make_user_client(token)

    result = (
        user_client.table("document_metadata")
        .select("file_id, file_title, url, mime_type, ingested_at, access_level, matter_id")
        .order("ingested_at", desc=True)
        .limit(100)
        .execute()
    )
    return JSONResponse({"documents": result.data or []})


@app.get("/sessions")
async def list_sessions(
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    token = _token_from_header(authorization)
    ctx, _rbac = _ctx_rbac_from_token(token)
    sessions = await list_user_sessions(ctx["user_id"])
    return JSONResponse({"sessions": sessions})


@app.get("/admin/users")
async def admin_list_users(authorization: str | None = Header(default=None)) -> JSONResponse:
    token = _token_from_header(authorization)
    ctx, rbac = _ctx_rbac_from_token(token)
    if _rbac_level(ctx, rbac) < 5:
        raise HTTPException(status_code=403, detail="Admin only")

    svc = make_service_client()
    users = svc.auth.admin.list_users()
    return JSONResponse({
        "users": [
            {
                "id": u.id,
                "email": u.email,
                "created_at": str(u.created_at),
                "access_level": (getattr(u, "user_metadata", {}) or {}).get("access_level", 1),
            }
            for u in (getattr(users, "users", []) or [])
        ]
    })


@app.post("/admin/users")
async def admin_create_user(
    request: Request,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    token = _token_from_header(authorization)
    ctx, rbac = _ctx_rbac_from_token(token)
    if _rbac_level(ctx, rbac) < 5:
        raise HTTPException(status_code=403, detail="Admin only")

    body = await request.json()
    email = body.get("email", "")
    password = body.get("password", "")
    access_level = int(body.get("access_level", 1))

    svc = make_service_client()
    user = svc.auth.admin.create_user({
        "email": email,
        "password": password,
        "email_confirm": True,
        "user_metadata": {"access_level": access_level},
    })
    return JSONResponse({"id": user.user.id, "email": user.user.email})


@app.get("/documents/drive-files")
async def drive_files(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _require_ingest(authorization)
    from ingest.downloader import list_folder_files

    files = list_folder_files()
    return {"files": files, "total": len(files)}


@app.post("/documents/ingest-folder")
async def ingest_drive_folder(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """
    Re-ingest the entire configured Drive folder.

    SECURITY: ``access_level`` and ``matter_id`` are derived from the
    requesting user. ``access_level`` is the caller's RBAC level (max sensitivity
    they can READ), which is the correct ceiling for documents they can also
    WRITE. ``matter_id`` is taken from the request body when supplied, otherwise
    the caller's first matter_id — so matter-scoped users stamp documents into
    their own matter, not into the firm's open pool.
    """
    ctx, rbac = _require_ingest(authorization)
    try:
        json_body = await request.json()
    except Exception:
        json_body = {}
    if not isinstance(json_body, dict):
        json_body = {}

    from ingest.downloader import ingest_folder

    matter_id = json_body.get("matter_id") or _pick_user_matter(ctx)
    access_level = int((rbac.get("perms") or {}).get("level") or 1)
    return await ingest_folder(access_level=access_level, matter_id=matter_id)


@app.post("/documents/ingest-file")
async def ingest_drive_file(
    body: DriveFileIngestBody,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """
    Ingest a single Drive file.

    SECURITY: ``access_level`` is derived from the caller's RBAC level.
    ``matter_id`` comes from the body when supplied, otherwise the caller's
    first matter_id.
    """
    ctx, rbac = _require_ingest(authorization)
    from ingest.drive_webhook import handle_drive_event

    matter_id = body.matter_id or _pick_user_matter(ctx)
    access_level = int((rbac.get("perms") or {}).get("level") or 1)
    return await handle_drive_event(
        file_id=body.file_id,
        event="manual",
        access_level=access_level,
        matter_id=matter_id,
    )


def _pick_user_matter(ctx: dict[str, Any]) -> str:
    """Return the caller's first matter_id, or '' if they have ``view_all``."""
    matters = ctx.get("matter_ids") or []
    if not matters:
        return ""
    return str(matters[0])


@app.options("/{path:path}")
async def preflight(path: str) -> Response:
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Authorization, Content-Type",
    }
    return Response(status_code=204, headers=headers)


@app.exception_handler(HTTPException)
async def http_exc(_request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail},
        headers={"Access-Control-Allow-Origin": "*"},
    )


@app.get("/{path:path}", include_in_schema=False)
async def frontend_fallback(path: str) -> FileResponse:
    if path.startswith(("auth/", "lawfirm-chat-trigger-006", "healthz", "docs", "openapi.json", "redoc")):
        raise HTTPException(status_code=404, detail="Not found")
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Frontend has not been built yet")
    return FileResponse(index_path)

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agents.rag_agent import run_chat
from auth.jwt_validator import AuthError, build_user_ctx, verify_jwt
from rbac.checker import build_rbac_block
from rls.access_context import set_access_context
from rls.filter_builder import build_rls_filter
from search.supabase_client import make_anon_client, make_user_client
from sessions.manager import build_session

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


class ChatBody(BaseModel):
    chatInput: str | None = None
    input: str | None = None
    query: str | None = None
    message: str | None = None
    sessionId: str | None = None


class ChatResponse(BaseModel):
    output: str


class DriveFileIngestBody(BaseModel):
    file_id: str
    matter_id: str | None = None


@app.post("/auth/login", response_model=LoginResponse)
async def login(body: LoginBody) -> LoginResponse:
    client = make_anon_client()
    res = client.auth.sign_in_with_password({"email": body.email, "password": body.password})
    sess = res.session if hasattr(res, "session") else res
    user = res.user if hasattr(res, "user") else None
    access = getattr(sess, "access_token", "") or ""
    refresh = getattr(sess, "refresh_token", "") or ""
    return LoginResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=getattr(sess, "expires_in", None),
        token_type=getattr(sess, "token_type", None),
        user_id=getattr(user, "id", None) if user else None,
        role=(getattr(user, "app_metadata", {}) or {}).get("role") if user else None,
    )


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

    output = run_chat(
        session_id=session["session_id"],
        system_prefix=rbac["system_prompt_prefix"],
        chat_input=chat_input,
        user_client=user_client,
    )
    return ChatResponse(output=output)


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

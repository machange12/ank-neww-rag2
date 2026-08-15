from __future__ import annotations

import logging
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

from agents.rag_agent import run_chat, stream_chat
from audit import events as audit_events
from auth.jwt_validator import AuthError, build_user_ctx, verify_jwt
from authz import service as authz_service
from authz.models import AccessDenied, UploadDecision
from authz.policy import classify_upload
from rbac.checker import build_rbac_block
from rbac.role_matrix import ROLE_MATRIX
from ratelimit import rate_limiter
from rls.filter_builder import build_rls_filter
from search.supabase_client import make_anon_client, make_user_client
from sessions.manager import build_session, list_user_sessions
from config import settings

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
    sources: list[dict[str, Any]] = []
    session_id: str


class FeedbackBody(BaseModel):
    session_id: str
    query: str
    answer_excerpt: str = ""
    rating: int  # 1 = thumbs up, -1 = thumbs down
    comment: str = ""

    @field_validator("rating")
    @classmethod
    def _rating_must_be_polar(cls, value: int) -> int:
        if value not in (-1, 1):
            raise ValueError("rating must be 1 (thumbs up) or -1 (thumbs down)")
        return value


class DriveFileIngestBody(BaseModel):
    file_id: str
    matter_id: str | None = None


def _resolve_profile_role_level(client: Any, user_id: str | None) -> tuple[str | None, int]:
    """Resolve role/access_level from public.user_profiles (DB source of truth).

    Runs on the client that just signed in, so RLS returns the user's own row.
    On any failure or missing row, fall back to role=None, access_level=1.
    """
    if not user_id:
        return None, 1
    try:
        res = (
            client.table("user_profiles")
            .select("role, access_level")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        data = getattr(res, "data", None) or res
        rows = data if isinstance(data, list) else []
        if rows:
            row = rows[0]
            return row.get("role"), int(row.get("access_level") or 1)
    except Exception:  # noqa: BLE001
        pass
    return None, 1


@app.post("/auth/login", response_model=LoginResponse)
async def login(body: LoginBody) -> LoginResponse:
    try:
        client = make_anon_client()
        res = client.auth.sign_in_with_password({"email": body.email, "password": body.password})
        sess = res.session if hasattr(res, "session") else res
        user = res.user if hasattr(res, "user") else None
        user_id = getattr(user, "id", None) if user else None
        role, access_level = _resolve_profile_role_level(client, user_id)
        access = getattr(sess, "access_token", "") or ""
        refresh = getattr(sess, "refresh_token", "") or ""
        return LoginResponse(
            access_token=access,
            refresh_token=refresh,
            expires_in=getattr(sess, "expires_in", None),
            token_type=getattr(sess, "token_type", None),
            user_id=user_id,
            role=role,
            access_level=access_level,
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
        payload = _payload_from_token(token)
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
        payload = _payload_from_token(token)
        ctx = build_user_ctx(payload, {}, body or {})
        rbac = build_rbac_block(ctx, body or {})
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return ctx, rbac


def _payload_from_token(token: str) -> dict[str, Any]:
    """
    Build a Supabase-style JWT payload.

    Prefer local signature verification with SUPABASE_JWT_SECRET. If that secret
    is stale or unavailable, fall back to Supabase Auth validation so freshly
    issued access tokens still work for document management endpoints.
    """
    try:
        return verify_jwt(token)
    except AuthError as local_error:
        try:
            anon = make_anon_client()
            response = anon.auth.get_user(token)
            user = getattr(response, "user", None)
            if not user:
                raise local_error

            app_metadata = getattr(user, "app_metadata", None) or {}
            user_metadata = getattr(user, "user_metadata", None) or {}
            return {
                "sub": getattr(user, "id", None),
                "email": getattr(user, "email", None),
                "app_metadata": app_metadata,
                "user_metadata": user_metadata,
            }
        except Exception as exc:
            raise local_error from exc


def _require_ingest(authorization: str | None) -> tuple[dict, dict]:
    # NOTE: this JWT-claim role check is a coarse pre-gate only. The write is
    # actually authorized by the DB profile + matter grants + auth_* RPCs.
    ctx, rbac = _auth_ctx_from_header(authorization)
    if not (rbac.get("perms") or {}).get("ingest"):
        raise HTTPException(status_code=403, detail="FORBIDDEN: your role cannot ingest documents")
    return ctx, rbac


def _assert_admin(authorization: str | None) -> tuple[dict, Any]:
    """Require the explicit DB admin flag (user_profiles.admin), not a level.

    Returns (ctx, user_client) so callers can run admin-management writes.
    """
    token = _token_from_header(authorization)
    ctx, _ = _ctx_rbac_from_token(token)
    user_client = make_user_client(token)
    try:
        authz_service.assert_admin(user_client, ctx["user_id"])
    except AccessDenied as exc:
        raise HTTPException(status_code=403, detail=exc.message) from exc
    return ctx, user_client


@app.post("/lawfirm-chat-trigger-006", response_model=ChatResponse)
async def chat_webhook(
    request: Request,
    authorization: str | None = Header(default=None),
) -> ChatResponse:
    raw_headers, body = await _resolve_headers_and_body(request)

    if authorization:
        token = authorization.replace("Bearer ", "").replace("bearer ", "").strip()
        try:
            payload = _payload_from_token(token)
        except AuthError as e:
            raise HTTPException(status_code=401, detail=str(e))
        ctx = build_user_ctx(payload, raw_headers, body)
        user_client = make_user_client(token)
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
            payload = _payload_from_token(token)
        except AuthError as e:
            raise HTTPException(status_code=401, detail=str(e))
        ctx = build_user_ctx(payload, raw_headers, body)

    await rate_limiter.check(ctx["user_id"])

    rbac = build_rbac_block(ctx, body)
    session = await build_session(ctx, rbac, user_client)
    rls = build_rls_filter(rbac, ctx, body)

    chat_input = rls.get("chat_input") or ""
    if not chat_input:
        raise HTTPException(status_code=400, detail="BAD_REQUEST: missing chat input")

    result = await run_chat(
        session_id=session["session_id"],
        system_prefix=rbac["system_prompt_prefix"],
        chat_input=chat_input,
        user_client=user_client,
        user_id=ctx["user_id"],
        tenant_id=session.get("tenant_id"),
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
            payload = _payload_from_token(token)
        except AuthError as e:
            raise HTTPException(status_code=401, detail=str(e))
        ctx = build_user_ctx(payload, raw_headers, body)
        user_client = make_user_client(token)
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
            payload = _payload_from_token(token)
        except AuthError as e:
            raise HTTPException(status_code=401, detail=str(e))
        ctx = build_user_ctx(payload, raw_headers, body)

    await rate_limiter.check(ctx["user_id"])

    rbac = build_rbac_block(ctx, body)
    session = await build_session(ctx, rbac, user_client)
    rls = build_rls_filter(rbac, ctx, body)

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
            user_id=ctx["user_id"],
            tenant_id=session.get("tenant_id"),
        ):
            token_text = str(token)
            if token_text.startswith("data: "):
                yield token_text
            else:
                yield f"data: {token_text}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/feedback", status_code=201)
async def submit_feedback(
    body: FeedbackBody,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    """
    Record thumbs-up/down feedback on a chat response.

    Written through the caller's own PostgREST client so the
    ``query_feedback_insert_own`` RLS policy applies (no service-role key).
    ``user_id`` is taken from the verified token, never from the request body.
    The referenced session must belong to the caller (checked against
    chat_sessions via RLS); otherwise the request is a 404.
    """
    token = _token_from_header(authorization)
    ctx, _ = _ctx_rbac_from_token(token)
    user_client = make_user_client(token)

    owned_res = (
        user_client.table("chat_sessions")
        .select("session_id")
        .eq("session_id", body.session_id)
        .eq("user_id_uuid", ctx["user_id"])
        .limit(1)
        .execute()
    )
    owned_data = getattr(owned_res, "data", None) or owned_res
    if not (isinstance(owned_data, list) and owned_data):
        raise HTTPException(status_code=404, detail="NOT_FOUND: session does not belong to this user")

    res = (
        user_client.table("query_feedback")
        .insert({
            "session_id": body.session_id,
            "user_id": ctx["user_id"],
            "user_id_uuid": ctx["user_id"],
            "query": body.query,
            "answer_excerpt": body.answer_excerpt,
            "rating": body.rating,
            "comment": body.comment,
        })
        .execute()
    )
    data = getattr(res, "data", None) or res
    inserted = data[0] if isinstance(data, list) and data else {}
    inserted_id = inserted.get("id")

    if inserted_id is None:
        raise HTTPException(status_code=500, detail="Failed to persist feedback")
    return JSONResponse({"status": "ok", "id": inserted_id}, status_code=201)


def _resolve_matter_ref(user_client: Any, requested_matter: str) -> str:
    """Resolve a matter UUID to its matter_ref for the ref-based admin RPC.

    ``requested_matter`` may be a matter UUID (from public.matters.id) or an
    already-resolved matter_ref string (e.g. "M-2024-118"). UUIDs are looked
    up in public.matters via the caller's client so RLS applies. Non-UUID
    values (matter_ref strings) are returned unchanged. A UUID that cannot be
    resolved fails closed and is returned as-is (the admin RPC will then
    return false and block the upload).
    """
    candidate = (requested_matter or "").strip()
    if not candidate:
        return ""
    try:
        uuid.UUID(candidate)
    except (ValueError, AttributeError, TypeError):
        return candidate
    try:
        res = (
            user_client.table("matters")
            .select("matter_ref")
            .eq("id", candidate)
            .limit(1)
            .execute()
        )
        data = getattr(res, "data", None) or res
        rows = data if isinstance(data, list) else []
        if rows and rows[0].get("matter_ref"):
            return rows[0]["matter_ref"]
        logger.warning("matter UUID %r did not resolve to a matter_ref", candidate)
    except Exception as exc:  # noqa: BLE001
        logger.warning("matter_ref lookup failed for uuid=%r: %s", candidate, exc)
    return candidate


def _matter_uuid_for(user_client: Any, requested_matter: str) -> str | None:
    """Resolve a matter ref/UUID to the matter's UUID (public.matters.id).

    UUIDs are returned as-is; matter_ref strings (e.g. "M-2024-118") are
    looked up via the caller's client so RLS applies. Returns None when the
    value is empty or cannot be resolved.
    """
    candidate = (requested_matter or "").strip()
    if not candidate:
        return None
    try:
        uuid.UUID(candidate)
        return candidate
    except (ValueError, AttributeError, TypeError):
        pass
    try:
        res = (
            user_client.table("matters")
            .select("id")
            .eq("matter_ref", candidate)
            .limit(1)
            .execute()
        )
        data = getattr(res, "data", None) or res
        rows = data if isinstance(data, list) else []
        if rows and rows[0].get("id"):
            return rows[0]["id"]
        logger.warning("matter_ref %r did not resolve to a matter UUID", candidate)
    except Exception as exc:  # noqa: BLE001
        logger.warning("matter UUID lookup failed for ref=%r: %s", candidate, exc)
    return None


@app.post("/documents/upload")
async def upload_document(
    file: UploadFile = File(...),
    matter_id: str = Form(""),
    access_level: int = Form(1),
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    """
    Upload one document, classified server-side.

    SECURITY: the client-supplied ``access_level`` is a HINT only and is NEVER
    used as an authorization fact (it is recorded in the audit trail). The
    effective level is computed from the caller's DB profile (user_profiles),
    their matter grants (matter_access) and a deterministic sensitivity floor
    over the extracted text, via ``authz.policy.classify_upload``. The
    requested matter must be one the caller holds a matter_access grant for
    (unless firm_wide) and must pass the ``auth_can_administer_matter_ref`` RPC
    (firm-wide callers may write into the firm pool). The write itself goes
    through the normal document-ingest write path.

    The ``matter_id`` form field may be a matter UUID or a matter_ref string
    (e.g. "M-2024-118"); UUIDs are resolved to their matter_ref (public.matters,
    via the caller's client) before the ref-based admin RPC is called.
    """
    token = _token_from_header(authorization)
    ctx, _ = _ctx_rbac_from_token(token)
    user_client = make_user_client(token)

    profile = authz_service.load_profile(user_client, ctx["user_id"])
    if profile is None:
        raise HTTPException(status_code=403, detail="FORBIDDEN: no user profile found")
    grants = authz_service.load_grants(user_client, ctx["user_id"])

    requested_matter = (matter_id or "").strip()
    administered = True
    if requested_matter:
        if not profile.firm_wide:
            # The caller must hold SOME grant for the requested matter
            # (matter_access row matching the matter UUID). The administer
            # RPC below is narrower (can_administer); this guard ensures the
            # user is not ingesting into a matter they have no relationship
            # with at all.
            matter_uuid = _matter_uuid_for(user_client, requested_matter)
            has_matter_grant = bool(matter_uuid) and any(
                g.matter_id == matter_uuid for g in grants
            )
            logger.info(
                "upload matter grant check: requested_matter=%r matter_uuid=%r has_grant=%s",
                requested_matter,
                matter_uuid,
                has_matter_grant,
            )
            if not has_matter_grant:
                raise HTTPException(
                    status_code=403, detail="FORBIDDEN: no matter grant authorizing ingest"
                )
        matter_ref = _resolve_matter_ref(user_client, requested_matter)
        logger.info(
            "upload matter auth: requested_matter=%r resolved_matter_ref=%r", requested_matter, matter_ref
        )
        administered = authz_service.can_administer_matter_ref(user_client, matter_ref)
        logger.info(
            "upload matter auth: can_administer_matter_ref(%r) -> %s", matter_ref, administered
        )
        if not administered:
            raise HTTPException(status_code=403, detail=f"FORBIDDEN: cannot administer matter {requested_matter!r}")

    contents = await file.read()
    try:
        from ingest.extract import extract_text

        text, _ = extract_text(contents, file.content_type or "application/octet-stream")
    except Exception as exc:  # noqa: BLE001
        logger.warning("classification text extraction failed: %s", exc)
        text = ""

    try:
        decision = classify_upload(
            profile,
            grants,
            text,
            requested_access_level=int(access_level or 1),
            requested_matter_id=requested_matter,
            administered=administered,
        )
    except AccessDenied as exc:
        raise HTTPException(status_code=403, detail=exc.message) from exc

    audit_events.log_classification(
        actor=ctx["user_id"],
        matter_id=decision.matter_id,
        access_level=decision.access_level,
        decision=decision.model_dump(),
    )

    suffix = os.path.splitext(file.filename or "")[1]
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
            access_level=decision.access_level,
            matter_id=decision.matter_id,
        )
        return JSONResponse({
            "status": "ok",
            "chunks": result.get("chunks", 0),
            "file_id": file_id,
            "access_level": decision.access_level,
            "matter_id": decision.matter_id,
            "sensitivity_floor": decision.sensitivity_floor,
            "classifier": decision.classifier,
        })
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


@app.get("/documents/intelligence")
async def list_document_intelligence(authorization: str | None = Header(default=None)) -> JSONResponse:
    """Return doc_type and legal_entities for all indexed documents the user can access."""
    token = _token_from_header(authorization)
    ctx, _ = _ctx_rbac_from_token(token)
    client = make_user_client(token)
    res = (
        client.table("document_metadata")
        .select("file_id, file_title, doc_type, legal_entities, ingested_at")
        .lte("access_level", ctx["access_level"])
        .order("ingested_at", desc=True)
        .execute()
    )
    data = getattr(res, "data", res) or []
    return JSONResponse({"documents": data})


@app.get("/sessions")
async def list_sessions(
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    token = _token_from_header(authorization)
    ctx, _rbac = _ctx_rbac_from_token(token)
    user_client = make_user_client(token)
    sessions = await list_user_sessions(user_client, ctx["user_id"])
    return JSONResponse({"sessions": sessions})


@app.get("/admin/users")
async def admin_list_users(authorization: str | None = Header(default=None)) -> JSONResponse:
    _ctx, _user_client = _assert_admin(authorization)

    svc = authz_service.admin_client()
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
    _ctx, _user_client = _assert_admin(authorization)

    body = await request.json()
    email = body.get("email", "")
    password = body.get("password", "")
    access_level = int(body.get("access_level", 1))

    svc = authz_service.admin_client()
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
    from ingest.downloader import DriveAuthError, list_folder_files

    try:
        files = list_folder_files()
    except DriveAuthError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"files": files, "total": len(files)}


@app.post("/documents/ingest-folder")
async def ingest_drive_folder(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """
    Re-ingest the entire configured Drive folder.

    SECURITY: ``access_level`` and ``matter_id`` are classified SERVER-SIDE.
    The caller's DB profile (user_profiles), matter grants (matter_access) and
    the ``auth_can_administer_matter_ref`` RPC are the only authorization facts;
    the client body and any Drive custom properties are hints only and are never
    trusted. Each file's text is classified with the same deterministic
    sensitivity floor used by ``/documents/upload`` (no weaker than uploads).
    """
    ctx, rbac = _require_ingest(authorization)
    user_client = make_user_client(_token_from_header(authorization))

    try:
        json_body = await request.json()
    except Exception:
        json_body = {}
    if not isinstance(json_body, dict):
        json_body = {}

    profile = authz_service.load_profile(user_client, ctx["user_id"])
    if profile is None:
        raise HTTPException(status_code=403, detail="FORBIDDEN: no user profile found")
    grants = authz_service.load_grants(user_client, ctx["user_id"])

    requested_matter = (json_body.get("matter_id") or "").strip() or await _pick_user_matter(user_client, ctx)
    requested_access_level = int(json_body.get("access_level") or (rbac.get("perms") or {}).get("level") or 1)

    administered = _require_matter_authority(user_client, profile, requested_matter)

    from ingest.downloader import DriveAuthError, ingest_folder

    classify = _make_ingest_classifier(
        user_client, ctx, profile, grants, requested_matter, requested_access_level, administered
    )
    try:
        return await ingest_folder(
            access_level=requested_access_level,
            matter_id=requested_matter,
            classify=classify,
        )
    except DriveAuthError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/documents/ingest-file")
async def ingest_drive_file(
    body: DriveFileIngestBody,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """
    Ingest a single Drive file, classified server-side.

    SECURITY: the effective ``access_level``/``matter_id`` come from the
    caller's DB profile/grants and the deterministic sensitivity floor over the
    file's extracted text (``authz.policy.classify_upload``), never from the
    client or from Drive custom properties. The requested matter must be
    administered by the caller (``auth_can_administer_matter_ref``).
    """
    ctx, rbac = _require_ingest(authorization)
    user_client = make_user_client(_token_from_header(authorization))

    profile = authz_service.load_profile(user_client, ctx["user_id"])
    if profile is None:
        raise HTTPException(status_code=403, detail="FORBIDDEN: no user profile found")
    grants = authz_service.load_grants(user_client, ctx["user_id"])

    requested_matter = (body.matter_id or "").strip() or await _pick_user_matter(user_client, ctx)
    requested_access_level = int((rbac.get("perms") or {}).get("level") or 1)

    administered = _require_matter_authority(user_client, profile, requested_matter)

    from ingest.downloader import DriveAuthError
    from ingest.drive_webhook import handle_drive_event

    classify = _make_ingest_classifier(
        user_client, ctx, profile, grants, requested_matter, requested_access_level, administered
    )
    try:
        return await handle_drive_event(
            file_id=body.file_id,
            event="manual",
            access_level=requested_access_level,
            matter_id=requested_matter,
            classify=classify,
        )
    except DriveAuthError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _require_matter_authority(user_client: Any, profile: Any, requested_matter: str) -> bool | None:
    """Validate the ingest target against authoritative facts only.

    Returns the authoritative administered verdict for the requested matter
    (True when the RPC confirms it, None for the firm pool) so callers can
    thread it into ``classify_upload``.
    """
    if requested_matter:
        administered = authz_service.can_administer_matter_ref(user_client, requested_matter)
        if not administered:
            raise HTTPException(status_code=403, detail=f"FORBIDDEN: cannot administer matter {requested_matter!r}")
        return True
    if not profile.firm_wide:
        raise HTTPException(status_code=403, detail="FORBIDDEN: only firm-wide administrators may ingest into the firm pool")
    return None


def _make_ingest_classifier(
    user_client: Any,
    ctx: dict[str, Any],
    profile: Any,
    grants: list[Any],
    requested_matter: str,
    requested_access_level: int,
    administered: bool | None,
):
    """Build a per-file server-side classifier ``text -> (access_level, matter_id)``.

    Uses the same ``authz.policy.classify_upload`` rules as uploads, so bulk
    ingest is never weaker than single-file upload. Each classification is
    recorded in the audit trail.
    """
    def classify(text: str) -> tuple[int, str]:
        decision = classify_upload(
            profile,
            grants,
            text,
            requested_access_level=requested_access_level,
            requested_matter_id=requested_matter,
            administered=administered,
        )
        audit_events.log_classification(
            actor=ctx["user_id"],
            matter_id=decision.matter_id,
            access_level=decision.access_level,
            decision=decision.model_dump(),
        )
        return decision.access_level, decision.matter_id

    return classify


async def _pick_user_matter(user_client: Any, ctx: dict[str, Any]) -> str:
    """Grant-aware default matter for ingest.

    Candidate refs come from the JWT claims (hints only); each is validated
    against the authoritative ``auth_can_administer_matter_ref`` RPC so only
    refs the caller actually administers are returned.
    """
    candidates = ctx.get("matter_ids") or []
    for ref in candidates:
        ref = str(ref).strip()
        if ref and authz_service.can_administer_matter_ref(user_client, ref):
            return ref
    return ""


def _request_user_info(request: Request) -> dict[str, str | None]:
    """Best-effort identity for audit logging from the Authorization header."""
    authorization = request.headers.get("authorization") or ""
    token = authorization.replace("Bearer ", "").replace("bearer ", "").strip()
    user_id: str | None = None
    email: str | None = None
    if token:
        try:
            payload = _payload_from_token(token)
            user_id = payload.get("sub")
            email = payload.get("email")
        except Exception:  # noqa: BLE001
            pass
    ip = (
        request.headers.get("x-forwarded-for")
        or request.headers.get("x-real-ip")
        or "unknown"
    ).split(",")[0].strip()
    return {"user_id": user_id, "actor_email": email, "ip_address": ip}


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
        info = _request_user_info(request)
        audit_events.log_rate_limit(user_id=info["user_id"], ip_address=info["ip_address"])
    elif exc.status_code in (401, 403):
        info = _request_user_info(request)
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

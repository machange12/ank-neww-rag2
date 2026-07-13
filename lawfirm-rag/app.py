from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from agents.rag_agent import run_chat
from auth.jwt_validator import AuthError, build_user_ctx, verify_jwt
from rbac.checker import build_rbac_block
from rls.access_context import set_access_context
from rls.filter_builder import build_rls_filter
from search.supabase_client import make_anon_client, make_user_client
from sessions.manager import build_session

app = FastAPI(title="Law Firm Secure RAG", version="2.3456")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


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

"""Chat, streaming chat, feedback, and session-history endpoints."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, field_validator

from agents.rag_agent import run_chat, stream_chat
from auth.jwt_validator import AuthError, build_user_ctx
from deps import (
    ctx_rbac_from_token,
    payload_from_token,
    resolve_headers_and_body,
    resolve_user_client,
    token_from_header,
)
from postgrest_utils import response_data
from rbac.checker import build_rbac_block
from ratelimit import rate_limiter
from rls.filter_builder import build_rls_filter
from search.supabase_client import make_user_client
from sessions.manager import build_session, list_user_sessions

router = APIRouter(tags=["chat"])


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


async def _resolve_chat_identity(request: Request, authorization: str | None) -> tuple[dict, Any, str, dict]:
    """Shared auth/ctx resolution for both chat endpoints.

    Supports two credential paths: a normal ``Authorization`` header, or
    (mirroring the legacy n8n webhook contract) credentials carried in the
    JSON body under ``authorization`` / ``body.authorization``.
    """
    raw_headers, body = await resolve_headers_and_body(request)

    if authorization:
        token = authorization.replace("Bearer ", "").replace("bearer ", "").strip()
        try:
            payload = payload_from_token(token)
        except AuthError as e:
            raise HTTPException(status_code=401, detail=str(e))
        ctx = build_user_ctx(payload, raw_headers, body)
        user_client = make_user_client(token)
    else:
        try:
            user_client, token = await resolve_user_client(body.get("authorization") if isinstance(body, dict) else None)  # type: ignore
        except HTTPException:
            body_inner = body.get("body", {}) if isinstance(body, dict) else {}
            auth_inner = body_inner.get("authorization") if isinstance(body_inner, dict) else None
            if not auth_inner:
                raise HTTPException(status_code=401, detail="UNAUTHORIZED: missing credentials") from None
            user_client, token = await resolve_user_client(auth_inner)  # type: ignore
        try:
            payload = payload_from_token(token)
        except AuthError as e:
            raise HTTPException(status_code=401, detail=str(e))
        ctx = build_user_ctx(payload, raw_headers, body)

    return ctx, user_client, token, body


@router.post("/lawfirm-chat-trigger-006", response_model=ChatResponse)
async def chat_webhook(
    request: Request,
    authorization: str | None = Header(default=None),
) -> ChatResponse:
    ctx, user_client, _token, body = await _resolve_chat_identity(request, authorization)

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


@router.post("/lawfirm-chat-stream")
async def chat_webhook_stream(
    request: Request,
    authorization: str | None = Header(default=None),
) -> StreamingResponse:
    """Streaming chat endpoint that returns Server-Sent Events (SSE).

    Mirrors /lawfirm-chat-trigger-006 auth, RBAC and session logic, then
    streams token events as they arrive from the LLM.
    """
    ctx, user_client, _token, body = await _resolve_chat_identity(request, authorization)

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


@router.post("/feedback", status_code=201)
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
    token = token_from_header(authorization)
    ctx, _ = ctx_rbac_from_token(token)
    user_client = make_user_client(token)

    owned_res = (
        user_client.table("chat_sessions")
        .select("session_id")
        .eq("session_id", body.session_id)
        .eq("user_id_uuid", ctx["user_id"])
        .limit(1)
        .execute()
    )
    owned_data = response_data(owned_res)
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
    data = response_data(res)
    inserted = data[0] if isinstance(data, list) and data else {}
    inserted_id = inserted.get("id")

    if inserted_id is None:
        raise HTTPException(status_code=500, detail="Failed to persist feedback")
    return JSONResponse({"status": "ok", "id": inserted_id}, status_code=201)


@router.get("/sessions")
async def list_sessions(
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    token = token_from_header(authorization)
    ctx, _rbac = ctx_rbac_from_token(token)
    user_client = make_user_client(token)
    sessions = await list_user_sessions(user_client, ctx["user_id"])
    return JSONResponse({"sessions": sessions})

"""Shared auth/JWT/RBAC context helpers used by every router.

Previously these existed as three overlapping copies inside app.py
(``_auth_ctx_from_header``, ``_ctx_rbac_from_token``, and the JWT-decode
logic duplicated inline in the chat endpoints). Consolidated here so every
router builds request context the same way.

None of these touch the service-role client — chat/documents/auth/admin
routers all resolve identity through the caller's own JWT and PostgREST
client. (Enforced by the static service-role test under ``tests/``.)
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException, Request, UploadFile

from auth.jwt_validator import AuthError, build_user_ctx, verify_jwt
from authz import service as authz_service
from authz.models import AccessDenied
from rbac.checker import build_rbac_block
from search.supabase_client import make_anon_client, make_user_client

logger = logging.getLogger(__name__)


async def resolve_headers_and_body(request: Request) -> tuple[dict, dict]:
    raw_headers = dict(request.headers.items())
    try:
        body = await request.json()
    except Exception:
        body = {}
    return raw_headers, body if isinstance(body, dict) else {}


def token_from_header(authorization: str | None) -> str:
    token = (authorization or "").replace("Bearer ", "").replace("bearer ", "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing token")
    return token


def payload_from_token(token: str) -> dict[str, Any]:
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


def ctx_rbac_from_token(token: str, body: dict[str, Any] | None = None) -> tuple[dict, dict]:
    try:
        payload = payload_from_token(token)
        ctx = build_user_ctx(payload, {}, body or {})
        rbac = build_rbac_block(ctx, body or {})
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return ctx, rbac


async def resolve_user_client(authorization: str | None) -> tuple[Any, str]:
    """Validate a bearer token (local JWT verify, falling back to Supabase Auth)
    and return a signed-in user client for it.

    Used by the chat endpoints' legacy n8n-style body-carried-credentials path.
    """
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


def require_ingest(authorization: str | None) -> tuple[dict, dict]:
    # NOTE: this JWT-claim role check is a coarse pre-gate only. The write is
    # actually authorized by the DB profile + matter grants + auth_* RPCs.
    token = token_from_header(authorization)
    ctx, rbac = ctx_rbac_from_token(token)
    if not (rbac.get("perms") or {}).get("ingest"):
        raise HTTPException(status_code=403, detail="FORBIDDEN: your role cannot ingest documents")
    return ctx, rbac


def assert_admin(authorization: str | None) -> tuple[dict, Any]:
    """Require the explicit DB admin flag (user_profiles.admin), not a level.

    Returns (ctx, user_client) so callers can run admin-management writes.
    """
    token = token_from_header(authorization)
    ctx, _ = ctx_rbac_from_token(token)
    user_client = make_user_client(token)
    try:
        authz_service.assert_admin(user_client, ctx["user_id"])
    except AccessDenied as exc:
        raise HTTPException(status_code=403, detail=exc.message) from exc
    return ctx, user_client


def request_user_info(request: Request) -> dict[str, str | None]:
    """Best-effort identity for audit logging from the Authorization header."""
    authorization = request.headers.get("authorization") or ""
    token = authorization.replace("Bearer ", "").replace("bearer ", "").strip()
    user_id: str | None = None
    email: str | None = None
    if token:
        try:
            payload = payload_from_token(token)
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


async def read_upload_capped(file: UploadFile, max_bytes: int) -> bytes:
    """Read an UploadFile into memory, aborting with 413 past ``max_bytes``.

    Reads in fixed-size chunks so an unbounded upload is rejected before the
    whole body is buffered (memory-DoS guard), instead of reading the entire
    file first and only then discovering it's too large.
    """
    chunk_size = 1024 * 1024
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"File exceeds the {max_bytes // (1024 * 1024)} MB upload limit",
            )
        chunks.append(chunk)
    return b"".join(chunks)

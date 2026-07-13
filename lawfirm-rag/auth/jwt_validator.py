from __future__ import annotations

import time
from typing import Any

from jose import jwt
from jose.exceptions import ExpiredSignatureError, JWTError

from config import settings


class AuthError(Exception):
    code = "UNAUTHORIZED"


def _b64url_decode(payload_segment: str) -> bytes:
    import base64

    pad = "=" * (-len(payload_segment) % 4)
    return base64.urlsafe_b64decode(payload_segment + pad)


def parse_jwt_unverified(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise AuthError("Malformed JWT")
    try:
        return __import__("json").loads(_b64url_decode(parts[1]).decode("utf-8"))
    except Exception as exc:
        raise AuthError(f"Invalid JWT payload: {exc}") from exc


def verify_jwt(token: str) -> dict[str, Any]:
    if not token:
        raise AuthError("No Bearer token in Authorization header")
    if not settings.supabase_jwt_secret:
        try:
            payload = parse_jwt_unverified(token)
        except AuthError as e:
            raise AuthError(str(e)) from e
    else:
        try:
            payload = jwt.decode(
                token,
                settings.supabase_jwt_secret,
                algorithms=["HS256"],
                options={"verify_aud": False},
            )
        except ExpiredSignatureError as exc:
            raise AuthError("Token expired") from exc
        except JWTError as exc:
            raise AuthError(f"Invalid JWT: {exc}") from exc

    now = int(time.time())
    if payload.get("exp") and int(payload["exp"]) < now:
        raise AuthError("Token expired")
    return payload


def build_user_ctx(
    payload: dict[str, Any],
    headers: dict[str, str] | None,
    body: dict[str, Any],
) -> dict[str, Any]:
    meta = payload.get("app_metadata") or {}
    email = payload.get("email") or "unknown@ak.law"
    role = meta.get("role") or "associate"
    access_level = int(meta.get("access_level") or 1)
    matter_ids = list(meta.get("matter_ids") or [])
    now = int(time.time())

    h = {k.lower(): v for k, v in (headers or {}).items()}
    ip = (
        (h.get("x-forwarded-for") or h.get("x-real-ip") or "unknown")
        .split(",")[0]
        .strip()
    )

    session_id = body.get("sessionId") or f"{payload.get('sub', 'anon')}_{now}"

    return {
        "user_id": payload.get("sub"),
        "email": email,
        "role": role,
        "session_id": session_id,
        "access_level": access_level,
        "matter_ids": matter_ids,
        "ip_address": ip,
        "token_iat": int(payload.get("iat") or now),
        "token_exp": int(payload.get("exp") or (now + 3600)),
    }

from __future__ import annotations

import math
import time
from functools import lru_cache
from typing import Any

import httpx

from jose import JWTError, jwt

from config import settings


class AuthError(Exception):
    pass


@lru_cache(maxsize=4)
def _jwks_key(kid: str) -> dict[str, Any] | None:
    # Supabase's asymmetric signing keys (ES256). Cached per kid; a new kid
    # (key rotation) misses the cache and refetches.
    keys = httpx.get(f"{settings.supabase_url}/auth/v1/.well-known/jwks.json", timeout=10).json().get("keys", [])
    return next((k for k in keys if k.get("kid") == kid), None)


def verify_jwt(token: str) -> dict[str, Any]:
    """
    Offline JWT verification using SUPABASE_JWT_SECRET.
    Raises AuthError on any failure.
    """
    if not token:
        raise AuthError("UNAUTHORIZED: No Bearer token provided")

    parts = token.split(".")
    if len(parts) != 3:
        raise AuthError("UNAUTHORIZED: Malformed JWT")

    try:
        header = jwt.get_unverified_header(token)
    except JWTError as e:
        raise AuthError(f"UNAUTHORIZED: {e}") from e
    secret = settings.supabase_jwt_secret
    if header.get("alg") in ("ES256", "RS256"):
        key = _jwks_key(header.get("kid") or "")
        if key is None:
            _jwks_key.cache_clear()
            raise AuthError("UNAUTHORIZED: unknown signing key")
        try:
            payload = jwt.decode(token, key, algorithms=[header["alg"]], options={"verify_aud": False})
        except JWTError as e:
            raise AuthError(f"UNAUTHORIZED: {e}") from e
    elif not secret:
        if settings.environment == "production":
            # Never decode unverified claims in production — a missing
            # secret must fail closed, not silently accept forged tokens.
            raise AuthError(
                "UNAUTHORIZED: SUPABASE_JWT_SECRET is not configured; "
                "refusing to accept unverified tokens in production"
            )
        # Fallback: decode without verification (dev/test only — log a warning)
        import warnings
        warnings.warn(
            "SUPABASE_JWT_SECRET not set — JWT signature NOT verified. Set it in .env.",
            stacklevel=2,
        )
        try:
            payload: dict = jwt.get_unverified_claims(token)
        except JWTError as e:
            raise AuthError(f"UNAUTHORIZED: Invalid JWT payload — {e}") from e
    else:
        try:
            payload = jwt.decode(
                token,
                secret,
                algorithms=["HS256"],
                options={"verify_aud": False},
            )
        except JWTError as e:
            raise AuthError(f"UNAUTHORIZED: {e}") from e

    now = math.floor(time.time())
    if payload.get("exp") and payload["exp"] < now:
        raise AuthError("UNAUTHORIZED: Token expired")

    return payload


def build_user_ctx(
    payload: dict[str, Any],
    headers: dict[str, str],
    body: dict[str, Any],
) -> dict[str, Any]:
    """Build the _user_ctx dict that mirrors the n8n JWT_Validator_Chat node output."""
    now = math.floor(time.time())
    meta: dict = payload.get("app_metadata") or {}
    user_meta: dict = payload.get("user_metadata") or {}

    ip = (
        headers.get("x-forwarded-for")
        or headers.get("x-real-ip")
        or "unknown"
    ).split(",")[0].strip()

    session_id = (
        body.get("sessionId")
        or f"{payload.get('sub', 'anon')}_{now}"
    )

    return {
        "user_id":      payload.get("sub"),
        "email":        payload.get("email") or "unknown@ak.law",
        "role":         meta.get("role") or "associate",
        "session_id":   session_id,
        "access_level": user_meta.get("access_level") or meta.get("access_level") or 1,
        "matter_ids":   meta.get("matter_ids") or [],
        "ip_address":   ip,
        "token_iat":    payload.get("iat") or now,
        "token_exp":    payload.get("exp") or (now + 3600),
    }

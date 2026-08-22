"""Login endpoint."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from postgrest_utils import response_data
from search.supabase_client import make_anon_client

router = APIRouter(tags=["auth"])


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
        data = response_data(res)
        rows = data if isinstance(data, list) else []
        if rows:
            row = rows[0]
            return row.get("role"), int(row.get("access_level") or 1)
    except Exception:  # noqa: BLE001
        pass
    return None, 1


@router.post("/auth/login", response_model=LoginResponse)
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

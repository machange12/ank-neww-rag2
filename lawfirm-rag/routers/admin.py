"""Admin user management (explicit DB admin flag only)."""
from __future__ import annotations

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from authz import service as authz_service
from deps import assert_admin

router = APIRouter(tags=["admin"])


@router.get("/admin/users")
async def admin_list_users(authorization: str | None = Header(default=None)) -> JSONResponse:
    _ctx, _user_client = assert_admin(authorization)

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


@router.post("/admin/users")
async def admin_create_user(
    request: Request,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    _ctx, _user_client = assert_admin(authorization)

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

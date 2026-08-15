"""DB-backed authorization loading through the caller's own client.

All reads use the user's own PostgREST client so RLS applies; no
service-role key is used on any user-facing path.
"""
from __future__ import annotations

import logging
from typing import Any

from authz.models import AccessDenied, MatterGrant, UserProfile

logger = logging.getLogger(__name__)


def _response_data(response: Any) -> Any:
    return getattr(response, "data", None) or response


def load_profile(user_client: Any, user_id: str) -> UserProfile | None:
    """Load the caller's user_profiles row (RLS: own row only)."""
    try:
        res = user_client.table("user_profiles").select("*").eq("user_id", user_id).limit(1).execute()
        rows = _response_data(res)
        if isinstance(rows, list) and rows:
            row = rows[0]
            return UserProfile(
                user_id=row.get("user_id") or user_id,
                tenant_id=row.get("tenant_id"),
                role=row.get("role") or "associate",
                access_level=int(row.get("access_level") or 1),
                firm_wide=bool(row.get("firm_wide") or False),
                admin=bool(row.get("admin") or False),
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("load_profile failed for user=%s: %s", user_id, exc)
    return None


def load_grants(user_client: Any, user_id: str) -> list[MatterGrant]:
    """Load the caller's matter_access rows (RLS: own rows only)."""
    grants: list[MatterGrant] = []
    try:
        res = user_client.table("matter_access").select("*").eq("user_id", user_id).execute()
        rows = _response_data(res)
        for row in rows if isinstance(rows, list) else []:
            grants.append(
                MatterGrant(
                    matter_id=row.get("matter_id"),
                    access_level=int(row.get("access_level") or 1),
                    can_administer=bool(row.get("can_administer") or False),
                )
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("load_grants failed for user=%s: %s", user_id, exc)
    return grants


def can_administer_matter_ref(user_client: Any, matter_ref: str) -> bool:
    """Ask the database (auth_can_administer_matter_ref RPC, security
    definer, derived from auth.uid()) whether the caller may
    administer the given matter reference."""
    if not matter_ref:
        return False
    try:
        res = user_client.rpc("auth_can_administer_matter_ref", {"p_matter_ref": matter_ref}).execute()
        data = getattr(res, "data", None) or res
        return bool(data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("auth_can_administer_matter_ref failed for ref=%r: %s", matter_ref, exc)
        return False


def assert_admin(user_client: Any, user_id: str) -> UserProfile:
    """Load the profile and require the explicit admin flag."""
    profile = load_profile(user_client, user_id)
    if profile is None:
        raise AccessDenied(f"FORBIDDEN: no user profile for {user_id}")
    if not profile.is_admin():
        raise AccessDenied("FORBIDDEN: explicit admin permission required")
    return profile


def admin_client() -> Any:
    """Service-role client for the narrowly-scoped ADMIN MANAGEMENT write path.

    Used only AFTER ``assert_admin`` has verified the caller, and only to
    manage Auth users (create/list). It is NOT used for retrieval, history,
    upload classification or any user-facing read. Keeping this import inside
    ``authz`` (rather than ``app.py``) keeps app.py free of service-role
    imports, which the static test enforces.
    """
    from search.service_client import make_service_client

    return make_service_client()
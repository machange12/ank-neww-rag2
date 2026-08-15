"""Matter ref/UUID resolution and administer-authority checks.

Previously inlined in app.py alongside the upload/ingest endpoints that use
it. Pulled out as its own data-access module (same pattern as
``sessions/manager.py``) since the matter-ref/UUID resolution logic was
duplicated across ``/documents/upload``, ``/documents/ingest-folder`` and
``/documents/ingest-file``.

All reads run through the caller's own client so RLS applies — no
service-role usage here.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import HTTPException

from authz import service as authz_service

logger = logging.getLogger(__name__)


def resolve_matter_ref(user_client: Any, requested_matter: str) -> str:
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


def matter_uuid_for(user_client: Any, requested_matter: str) -> str | None:
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


def require_matter_authority(user_client: Any, profile: Any, requested_matter: str) -> bool | None:
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


async def pick_user_matter(user_client: Any, ctx: dict[str, Any]) -> str:
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

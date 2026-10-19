"""Draft persistence helpers.

Every read/write here runs through the caller's own PostgREST client, so the
ownership RLS policies on drafts/draft_sections/draft_versions
(``user_id_uuid = auth.uid()``) are the enforcement boundary — no service-role
key is used on this path. See docs/decisions/0006-drafting-assistant.md.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from postgrest_utils import response_data

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# The single-firm tenant id provisioned by migration 0001.
DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"


async def resolve_tenant_id(user_client: Any, user_id: str) -> str:
    """Best-effort tenant id from the caller's OWN user_profiles row.

    Runs through the caller's client, so user_profiles_select_own returns at
    most the caller's row. Falls back to the default firm tenant.
    """
    try:
        res = (
            user_client.table("user_profiles")
            .select("tenant_id")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        data = response_data(res)
        if isinstance(data, list) and data and data[0].get("tenant_id"):
            return data[0]["tenant_id"]
    except Exception as exc:  # noqa: BLE001
        logger.warning("tenant_id lookup failed for user=%s: %s", user_id, exc)
    return DEFAULT_TENANT_ID


def _first(data: Any) -> dict[str, Any] | None:
    if isinstance(data, list) and data:
        return data[0]
    return None


async def create_draft(
    user_client: Any,
    *,
    user_id: str,
    tenant_id: str | None,
    title: str,
    doc_type: str,
    matter_id: str = "",
    template_id: str | None = None,
    brief: dict[str, Any] | None = None,
    skeleton: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Insert a draft row and its sections from ``skeleton``. Returns both."""
    draft_row = {
        "tenant_id": tenant_id,
        "user_id_uuid": user_id,
        "matter_id": matter_id or "",
        "title": title,
        "doc_type": doc_type,
        "template_id": template_id,
        "brief": brief or {},
        "status": "draft",
        "current_version": 1,
    }
    res = user_client.table("drafts").insert(draft_row).execute()
    draft = _first(response_data(res))
    if not draft or not draft.get("id"):
        raise RuntimeError("draft insert did not return a row")

    draft_id = draft["id"]
    sections: list[dict[str, Any]] = []
    for index, section in enumerate(skeleton or []):
        srow = {
            "draft_id": draft_id,
            "order_index": index,
            "heading": str(section.get("heading") or ""),
            "clause_type": str(section.get("clause_type") or ""),
            "content": "",
            "sources": [],
            "status": "empty",
            # Persisted (migration 00015) so the skeleton's drafting hint and
            # required flag survive a reload — not just the create response.
            "guidance": str(section.get("guidance") or ""),
            "required": bool(section.get("required", True)),
        }
        sres = user_client.table("draft_sections").insert(srow).execute()
        saved = _first(response_data(sres))
        if saved:
            sections.append(saved)

    draft["sections"] = sections
    # Baseline snapshot (v1) so every draft has a restorable starting point.
    created_version = await snapshot_version(user_client, draft, user_id=user_id, label="created")
    draft["versions"] = [created_version] if created_version else []
    return draft


async def list_drafts(user_client: Any, limit: int = 50) -> list[dict[str, Any]]:
    res = (
        user_client.table("drafts")
        .select("id, title, doc_type, matter_id, visibility, status, current_version, user_id_uuid, created_at, updated_at")
        .order("updated_at", desc=True)
        .limit(limit)
        .execute()
    )
    data = response_data(res)
    return data if isinstance(data, list) else []


async def set_draft_visibility(
    user_client: Any,
    draft_id: str,
    *,
    visibility: str,
    matter_id: str,
) -> dict[str, Any] | None:
    """Publish a draft to (or retract it from) a matter team.

    Owner-only: the ``drafts_update_own`` RLS policy makes an UPDATE by a
    non-owner match zero rows, so this returns None and the router 403s. The
    row is returned so the caller can refresh its local copy.
    """
    res = (
        user_client.table("drafts")
        .update({"visibility": visibility, "matter_id": matter_id or "", "updated_at": _now()})
        .eq("id", draft_id)
        .execute()
    )
    return _first(response_data(res))


async def get_draft(user_client: Any, draft_id: str) -> dict[str, Any] | None:
    """Return a draft with its ordered sections and version list, or None.

    Ownership is enforced by RLS on ``drafts``; the child queries are scoped by
    the parent draft id, so a draft the caller cannot see yields None here.
    """
    res = user_client.table("drafts").select("*").eq("id", draft_id).limit(1).execute()
    draft = _first(response_data(res))
    if not draft:
        return None

    sres = (
        user_client.table("draft_sections")
        .select("*")
        .eq("draft_id", draft_id)
        .order("order_index")
        .execute()
    )
    sections = response_data(sres)
    draft["sections"] = sorted(sections, key=lambda s: s.get("order_index", 0)) if isinstance(sections, list) else []

    vres = (
        user_client.table("draft_versions")
        .select("id, version, label, created_at")
        .eq("draft_id", draft_id)
        .order("version", desc=True)
        .execute()
    )
    versions = response_data(vres)
    draft["versions"] = versions if isinstance(versions, list) else []

    draft["last_check"] = await get_latest_check(user_client, draft_id)
    return draft


async def update_section(
    user_client: Any,
    draft_id: str,
    order_index: int,
    *,
    content: str | None = None,
    heading: str | None = None,
    sources: list[dict[str, Any]] | None = None,
    status: str | None = None,
) -> dict[str, Any] | None:
    patch: dict[str, Any] = {"updated_at": _now()}
    if content is not None:
        patch["content"] = content
    if heading is not None:
        patch["heading"] = heading
    if sources is not None:
        patch["sources"] = sources
    if status is not None:
        patch["status"] = status

    res = (
        user_client.table("draft_sections")
        .update(patch)
        .eq("draft_id", draft_id)
        .eq("order_index", order_index)
        .execute()
    )
    data = response_data(res)
    updated = _first(data)
    if updated is None:
        # PostgREST returns [] when the filter matched nothing (e.g. bad index).
        return None
    # Keep the parent's updated_at fresh so listings order by real activity.
    try:
        user_client.table("drafts").update({"updated_at": _now()}).eq("id", draft_id).execute()
    except Exception as exc:  # noqa: BLE001
        logger.debug("draft updated_at bump failed for %s: %s", draft_id, exc)
    return updated


async def _max_version(user_client: Any, draft_id: str) -> int:
    """Highest version number already stored for a draft (0 when none)."""
    res = (
        user_client.table("draft_versions")
        .select("version")
        .eq("draft_id", draft_id)
        .order("version", desc=True)
        .limit(1)
        .execute()
    )
    data = response_data(res)
    if isinstance(data, list) and data:
        try:
            return int(data[0].get("version") or 0)
        except (TypeError, ValueError):
            return 0
    return 0


async def snapshot_version(
    user_client: Any,
    draft: dict[str, Any],
    *,
    user_id: str,
    label: str = "",
) -> dict[str, Any] | None:
    """Persist an immutable snapshot of ``draft`` (with sections) as the next
    version, bump ``drafts.current_version``, and return the created row.

    Version numbers are derived from the stored max, not ``current_version``,
    so snapshots stay strictly increasing even after a restore.
    """
    version = await _max_version(user_client, draft["id"]) + 1
    snapshot = {
        "title": draft.get("title"),
        "doc_type": draft.get("doc_type"),
        "status": draft.get("status"),
        "sections": [
            {
                "order_index": s.get("order_index"),
                "heading": s.get("heading"),
                "content": s.get("content"),
                "sources": s.get("sources"),
                "status": s.get("status"),
            }
            for s in draft.get("sections", [])
        ],
    }
    row = {
        "draft_id": draft["id"],
        "version": version,
        "snapshot": snapshot,
        "label": label,
        "created_by": user_id,
    }
    try:
        res = user_client.table("draft_versions").insert(row).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("draft version snapshot failed for %s: %s", draft.get("id"), exc)
        return None
    saved = _first(response_data(res)) or {**row, "id": None}
    try:
        user_client.table("drafts").update(
            {"current_version": version, "updated_at": _now()}
        ).eq("id", draft["id"]).execute()
    except Exception as exc:  # noqa: BLE001
        logger.debug("draft current_version bump failed for %s: %s", draft.get("id"), exc)
    return saved


async def get_version(user_client: Any, draft_id: str, version: int) -> dict[str, Any] | None:
    res = (
        user_client.table("draft_versions")
        .select("id, version, label, created_at, snapshot")
        .eq("draft_id", draft_id)
        .eq("version", version)
        .limit(1)
        .execute()
    )
    return _first(response_data(res))


async def restore_version(
    user_client: Any,
    draft: dict[str, Any],
    version: int,
    *,
    user_id: str,
) -> dict[str, Any] | None:
    """Load snapshot ``version`` back onto the draft's sections, then snapshot
    the restored state as a NEW version (append-only history). Returns the
    refreshed draft, or None when the version does not exist.
    """
    row = await get_version(user_client, draft["id"], version)
    if row is None:
        return None
    snapshot = row.get("snapshot") or {}
    for section in snapshot.get("sections") or []:
        try:
            order_index = int(section.get("order_index"))
        except (TypeError, ValueError):
            continue
        await update_section(
            user_client,
            draft["id"],
            order_index,
            content=section.get("content") or "",
            heading=section.get("heading") or "",
            sources=section.get("sources") or [],
            status=section.get("status") or "edited",
        )
    refreshed = await get_draft(user_client, draft["id"])
    if refreshed is None:
        return None
    await snapshot_version(user_client, refreshed, user_id=user_id, label=f"restored v{version}")
    return await get_draft(user_client, draft["id"])


# --------------------------------------------------------------- playbooks

async def get_playbooks(user_client: Any, doc_type: str | None = None) -> list[dict[str, Any]]:
    """Active playbooks visible to the caller, optionally for one doc type.

    RLS restricts this to playbooks at/under the caller's access ceiling and
    within their matters; ``doc_type`` also matches the '' (all-types) case.
    """
    query = (
        user_client.table("playbooks")
        .select("id, name, doc_type, description, access_level, matter_id")
        .eq("is_active", True)
        .order("name")
    )
    if doc_type:
        query = query.in_("doc_type", [doc_type, ""])
    res = query.execute()
    data = response_data(res)
    return data if isinstance(data, list) else []


async def get_playbook_rules(user_client: Any, playbook_id: str) -> list[dict[str, Any]]:
    res = (
        user_client.table("playbook_rules")
        .select("id, playbook_id, order_index, clause_type, position, rule_text, severity")
        .eq("playbook_id", playbook_id)
        .order("order_index")
        .execute()
    )
    data = response_data(res)
    return data if isinstance(data, list) else []


async def list_playbooks_with_rules(
    user_client: Any, doc_type: str | None = None
) -> list[dict[str, Any]]:
    playbooks = await get_playbooks(user_client, doc_type)
    for playbook in playbooks:
        playbook["rules"] = await get_playbook_rules(user_client, playbook["id"])
    return playbooks


async def save_check(
    user_client: Any,
    draft_id: str,
    *,
    playbook_id: str | None,
    flags: list[dict[str, Any]],
    summary: dict[str, Any],
    user_id: str,
) -> dict[str, Any] | None:
    row = {
        "draft_id": draft_id,
        "playbook_id": playbook_id,
        "flags": flags,
        "summary": summary,
        "created_by": user_id,
    }
    res = user_client.table("draft_checks").insert(row).execute()
    return _first(response_data(res)) or row


async def get_latest_check(user_client: Any, draft_id: str) -> dict[str, Any] | None:
    res = (
        user_client.table("draft_checks")
        .select("id, playbook_id, flags, summary, created_at")
        .eq("draft_id", draft_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    return _first(response_data(res))

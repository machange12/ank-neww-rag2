"""Drafting ("Draft Studio") endpoints: create/list/read drafts, generate and
edit grounded sections.

Auth, rate-limiting and the caller's PostgREST client follow the same pattern
as ``routers/chat.py``. Retrieval and generation run under the caller's RLS —
no service-role key on this path.
"""
from __future__ import annotations

import json
from typing import Any

import re

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from agents.draft_agent import (
    check_playbook,
    format_brief,
    propose_skeleton,
    refine_section,
    stream_generate_section,
    summarize_flags,
)
from audit.events import record_event
from authz.service import can_access_matter_ref
from deps import ctx_rbac_from_token, token_from_header
from drafting.export import build_docx, build_markdown, build_text
from drafting.store import (
    create_draft,
    get_draft,
    get_playbook_rules,
    get_playbooks,
    list_drafts,
    list_playbooks_with_rules,
    resolve_tenant_id,
    restore_version,
    save_check,
    set_draft_visibility,
    snapshot_version,
    update_section,
)
from providers import make_chat_llm
from ratelimit import rate_limiter
from search.clauses import search_clauses
from search.supabase_client import make_user_client

router = APIRouter(tags=["drafting"])


class CreateDraftBody(BaseModel):
    title: str
    doc_type: str
    matter_id: str = ""
    template_id: str | None = None
    brief: dict[str, Any] | None = None


class SaveSectionBody(BaseModel):
    content: str | None = None
    heading: str | None = None


class RefineBody(BaseModel):
    instruction: str


class VersionBody(BaseModel):
    label: str | None = None


class CheckBody(BaseModel):
    playbook_id: str | None = None


class ShareBody(BaseModel):
    matter_id: str


def _auth(token: str | None) -> tuple[dict, Any]:
    resolved = token_from_header(token)
    ctx, _rbac = ctx_rbac_from_token(resolved)
    return ctx, make_user_client(resolved)


def _find_section(draft: dict[str, Any], order_index: int) -> dict[str, Any] | None:
    for section in draft.get("sections", []):
        if int(section.get("order_index", -1)) == order_index:
            return section
    return None


def _safe_filename(title: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._ -]+", "", title or "").strip().replace(" ", "-")
    return slug[:80] or "draft"


async def _load_visible_draft(user_client: Any, draft_id: str) -> dict[str, Any]:
    """Load a draft the caller may READ (own, or a matter-shared one)."""
    draft = await get_draft(user_client, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND: no such draft for this user")
    return draft


async def _load_editable_draft(user_client: Any, draft_id: str, user_id: str) -> dict[str, Any]:
    """Load a draft and require the caller to be its OWNER.

    A shared draft is readable by its matter team but every mutation is
    owner-only (the ``drafts_*_own`` RLS policies). Checking ownership here
    turns that into a clean 403 rather than a confusing empty-update 404.
    """
    draft = await _load_visible_draft(user_client, draft_id)
    if str(draft.get("user_id_uuid") or "") != str(user_id or ""):
        raise HTTPException(status_code=403, detail="FORBIDDEN: only the draft owner can modify it")
    return draft


def _with_owner(draft: dict[str, Any] | None, user_id: str) -> dict[str, Any] | None:
    """Annotate a draft with whether the caller owns it (drives read-only UI)."""
    if isinstance(draft, dict):
        draft["is_owner"] = str(draft.get("user_id_uuid") or "") == str(user_id or "")
    return draft


@router.post("/drafts", status_code=201)
async def create_draft_endpoint(
    body: CreateDraftBody,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    if not body.title.strip() or not body.doc_type.strip():
        raise HTTPException(status_code=400, detail="BAD_REQUEST: title and doc_type are required")

    ctx, user_client = _auth(authorization)
    await rate_limiter.check(ctx["user_id"])

    # A curated template supplies the skeleton verbatim; otherwise the LLM
    # proposes one (falling back to a deterministic default).
    template_skeleton = None
    if body.template_id:
        try:
            res = (
                user_client.table("draft_templates")
                .select("skeleton")
                .eq("id", body.template_id)
                .limit(1)
                .execute()
            )
            rows = getattr(res, "data", None) or []
            if rows:
                template_skeleton = rows[0].get("skeleton") or None
        except Exception:  # noqa: BLE001
            template_skeleton = None

    llm = make_chat_llm(streaming=False)
    skeleton = await propose_skeleton(
        body.doc_type, body.title, body.brief, user_client, llm, template_skeleton
    )

    tenant_id = await resolve_tenant_id(user_client, ctx["user_id"])
    draft = await create_draft(
        user_client,
        user_id=ctx["user_id"],
        tenant_id=tenant_id,
        title=body.title.strip(),
        doc_type=body.doc_type.strip(),
        matter_id=body.matter_id,
        template_id=body.template_id,
        brief=body.brief or {},
        skeleton=skeleton,
    )
    record_event(
        event_type="draft",
        action="draft_created",
        outcome="ok",
        user_id=ctx["user_id"],
        actor_email=ctx.get("email"),
        detail=f"draft_id={draft.get('id')} doc_type={body.doc_type} sections={len(skeleton)}",
    )
    return JSONResponse({"draft": _with_owner(draft, ctx["user_id"])}, status_code=201)


@router.get("/drafts")
async def list_drafts_endpoint(
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    ctx, user_client = _auth(authorization)
    drafts = await list_drafts(user_client)
    drafts = [_with_owner(draft, ctx["user_id"]) for draft in drafts]
    return JSONResponse({"drafts": drafts})


@router.get("/drafts/{draft_id}")
async def get_draft_endpoint(
    draft_id: str,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    ctx, user_client = _auth(authorization)
    draft = _with_owner(await _load_visible_draft(user_client, draft_id), ctx["user_id"])
    return JSONResponse({"draft": draft})


@router.get("/draft/templates")
async def list_templates_endpoint(
    doc_type: str | None = None,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    """List curated templates visible to the caller (RLS filters access)."""
    _ctx, user_client = _auth(authorization)
    query = (
        user_client.table("draft_templates")
        .select("id, doc_type, practice_area, jurisdiction, title, description, skeleton")
        .eq("is_active", True)
        .order("title")
    )
    if doc_type:
        query = query.eq("doc_type", doc_type)
    res = query.execute()
    rows = getattr(res, "data", None) or []
    return JSONResponse({"templates": rows})


@router.get("/draft/clauses")
async def search_clauses_endpoint(
    q: str | None = None,
    doc_type: str | None = None,
    clause_type: str | None = None,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    """Search the curated clause bank (hybrid when ``q`` is given)."""
    _ctx, user_client = _auth(authorization)
    if q and q.strip():
        docs = search_clauses(
            user_client,
            q.strip(),
            match_count=10,
            doc_type_filter=doc_type or None,
            clause_type_filter=clause_type or None,
        )
        clauses = [
            {
                "id": (d.metadata or {}).get("file_id", ""),
                "title": (d.metadata or {}).get("file_title", ""),
                "clause_type": (d.metadata or {}).get("clause_type", ""),
                "doc_type": (d.metadata or {}).get("doc_type", ""),
                "body": d.page_content,
            }
            for d in docs
        ]
        return JSONResponse({"clauses": clauses})

    query = (
        user_client.table("draft_clauses")
        .select("id, title, clause_type, doc_type, body")
        .order("title")
        .limit(100)
    )
    if doc_type:
        query = query.eq("doc_type", doc_type)
    if clause_type:
        query = query.eq("clause_type", clause_type)
    res = query.execute()
    rows = getattr(res, "data", None) or []
    return JSONResponse({"clauses": rows})


@router.get("/draft/playbooks")
async def list_playbooks_endpoint(
    doc_type: str | None = None,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    """List playbooks (with their rules) visible to the caller."""
    _ctx, user_client = _auth(authorization)
    playbooks = await list_playbooks_with_rules(user_client, doc_type or None)
    return JSONResponse({"playbooks": playbooks})


@router.post("/drafts/{draft_id}/sections/{order_index}/generate")
async def generate_section_endpoint(
    draft_id: str,
    order_index: int,
    authorization: str | None = Header(default=None),
) -> StreamingResponse:
    ctx, user_client = _auth(authorization)
    await rate_limiter.check(ctx["user_id"])

    draft = await _load_editable_draft(user_client, draft_id, ctx["user_id"])
    section = _find_section(draft, order_index)
    if section is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND: no such section")
    brief_text = format_brief(draft.get("brief"))
    doc_type = draft.get("doc_type") or ""
    llm = make_chat_llm(streaming=True)

    async def event_stream():
        result: dict[str, Any] | None = None
        async for event in stream_generate_section(section, brief_text, doc_type, user_client, llm):
            if event.get("type") == "result":
                result = event
                saved = await update_section(
                    user_client,
                    draft_id,
                    order_index,
                    content=event.get("content", ""),
                    sources=event.get("sources", []),
                    status=event.get("status", "generated"),
                )
                yield "data: " + json.dumps({
                    "type": "result",
                    "content": event.get("content", ""),
                    "sources": event.get("sources", []),
                    "status": event.get("status", "generated"),
                    "section": saved,
                }) + "\n\n"
            else:
                yield f"data: {json.dumps(event)}\n\n"
        if result is not None:
            record_event(
                event_type="draft",
                action="section_generated",
                outcome=result.get("status", "generated"),
                user_id=ctx["user_id"],
                actor_email=ctx.get("email"),
                detail=f"draft_id={draft_id} order_index={order_index} sources={len(result.get('sources') or [])}",
            )
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/drafts/{draft_id}/sections/{order_index}")
async def save_section_endpoint(
    draft_id: str,
    order_index: int,
    body: SaveSectionBody,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    """Persist a manual edit to a section (status becomes 'edited')."""
    ctx, user_client = _auth(authorization)
    draft = await _load_editable_draft(user_client, draft_id, ctx["user_id"])
    if _find_section(draft, order_index) is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND: no such section")

    saved = await update_section(
        user_client,
        draft_id,
        order_index,
        content=body.content,
        heading=body.heading,
        status="edited" if body.content is not None else None,
    )
    if saved is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND: no such section")
    return JSONResponse({"section": saved})


@router.post("/drafts/{draft_id}/sections/{order_index}/refine")
async def refine_section_endpoint(
    draft_id: str,
    order_index: int,
    body: RefineBody,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    """Revise a section by instruction; no new facts, citations preserved."""
    ctx, user_client = _auth(authorization)
    await rate_limiter.check(ctx["user_id"])

    draft = await _load_editable_draft(user_client, draft_id, ctx["user_id"])
    section = _find_section(draft, order_index)
    if section is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND: no such section")
    if not str(section.get("content") or "").strip():
        raise HTTPException(status_code=400, detail="BAD_REQUEST: section has no content to refine")

    llm = make_chat_llm(streaming=False)
    try:
        result = await refine_section(section, body.instruction, user_client, llm)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    saved = await update_section(
        user_client,
        draft_id,
        order_index,
        content=result["content"],
        sources=result["sources"],
        status=result["status"],
    )
    record_event(
        event_type="draft",
        action="section_refined",
        outcome="ok",
        user_id=ctx["user_id"],
        actor_email=ctx.get("email"),
        detail=f"draft_id={draft_id} order_index={order_index}",
    )
    return JSONResponse({"section": saved or result})


@router.post("/drafts/{draft_id}/versions", status_code=201)
async def save_version_endpoint(
    draft_id: str,
    body: VersionBody | None = None,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    """Snapshot the draft's current sections as a new immutable version."""
    ctx, user_client = _auth(authorization)
    draft = await _load_editable_draft(user_client, draft_id, ctx["user_id"])
    label = (body.label if body and body.label else "manual")[:120]
    version_row = await snapshot_version(
        user_client, draft, user_id=ctx["user_id"], label=label
    )
    if version_row is None:
        raise HTTPException(status_code=500, detail="Could not save version")
    record_event(
        event_type="draft",
        action="version_saved",
        outcome="ok",
        user_id=ctx["user_id"],
        actor_email=ctx.get("email"),
        detail=f"draft_id={draft_id} version={version_row.get('version')}",
    )
    refreshed = await get_draft(user_client, draft_id)
    return JSONResponse({"version": version_row, "draft": _with_owner(refreshed, ctx["user_id"])}, status_code=201)


@router.post("/drafts/{draft_id}/versions/{version}/restore")
async def restore_version_endpoint(
    draft_id: str,
    version: int,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    """Restore a prior version's sections, recorded as a new version."""
    ctx, user_client = _auth(authorization)
    draft = await _load_editable_draft(user_client, draft_id, ctx["user_id"])
    restored = await restore_version(user_client, draft, version, user_id=ctx["user_id"])
    if restored is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND: no such version")
    record_event(
        event_type="draft",
        action="version_restored",
        outcome="ok",
        user_id=ctx["user_id"],
        actor_email=ctx.get("email"),
        detail=f"draft_id={draft_id} restored_version={version}",
    )
    return JSONResponse({"draft": _with_owner(restored, ctx["user_id"])})


@router.post("/drafts/{draft_id}/share")
async def share_draft_endpoint(
    draft_id: str,
    body: ShareBody,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    """Share a draft to a matter team (explicit opt-in; READ-ONLY for them)."""
    ctx, user_client = _auth(authorization)
    draft = await _load_editable_draft(user_client, draft_id, ctx["user_id"])

    matter_id = (body.matter_id or "").strip()
    if not matter_id:
        raise HTTPException(status_code=400, detail="BAD_REQUEST: matter_id is required")
    # An owner may only share to a matter they themselves can access; the RLS
    # SELECT policy then reveals the draft to that matter's team.
    if not can_access_matter_ref(user_client, matter_id):
        raise HTTPException(status_code=403, detail="FORBIDDEN: no access to that matter")

    updated = await set_draft_visibility(
        user_client, draft_id, visibility="matter", matter_id=matter_id
    )
    if updated is None:
        raise HTTPException(status_code=403, detail="FORBIDDEN: only the draft owner can share it")
    record_event(
        event_type="draft",
        action="draft_shared",
        outcome="ok",
        user_id=ctx["user_id"],
        actor_email=ctx.get("email"),
        detail=f"draft_id={draft_id} matter_id={matter_id}",
    )
    return JSONResponse({"draft": _with_owner(await get_draft(user_client, draft_id), ctx["user_id"])})


@router.post("/drafts/{draft_id}/unshare")
async def unshare_draft_endpoint(
    draft_id: str,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    """Return a shared draft to private (owner-only)."""
    ctx, user_client = _auth(authorization)
    draft = await _load_editable_draft(user_client, draft_id, ctx["user_id"])
    updated = await set_draft_visibility(
        user_client, draft_id, visibility="private", matter_id=""
    )
    if updated is None:
        raise HTTPException(status_code=403, detail="FORBIDDEN: only the draft owner can unshare it")
    record_event(
        event_type="draft",
        action="draft_unshared",
        outcome="ok",
        user_id=ctx["user_id"],
        actor_email=ctx.get("email"),
        detail=f"draft_id={draft_id}",
    )
    return JSONResponse({"draft": _with_owner(await get_draft(user_client, draft_id), ctx["user_id"])})


@router.post("/drafts/{draft_id}/check")
async def check_draft_endpoint(
    draft_id: str,
    body: CheckBody | None = None,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    """Check a draft against a playbook's firm positions and persist the result."""
    ctx, user_client = _auth(authorization)
    await rate_limiter.check(ctx["user_id"])

    draft = await _load_editable_draft(user_client, draft_id, ctx["user_id"])
    doc_type = draft.get("doc_type") or ""

    requested_id = body.playbook_id if body else None
    if requested_id:
        playbook = next(
            (pb for pb in await get_playbooks(user_client) if pb.get("id") == requested_id),
            None,
        )
    else:
        playbooks = await get_playbooks(user_client, doc_type)
        playbook = playbooks[0] if playbooks else None

    if playbook is None:
        return JSONResponse({
            "check": None,
            "playbook": None,
            "flags": [],
            "summary": summarize_flags([]),
            "message": "No playbook applies to this document type.",
        })

    rules = await get_playbook_rules(user_client, playbook["id"])
    if not rules:
        raise HTTPException(status_code=422, detail="BAD_REQUEST: playbook has no rules")

    llm = make_chat_llm(streaming=False)
    flags = await check_playbook(rules, draft.get("sections", []), llm)
    summary = summarize_flags(flags)
    check = await save_check(
        user_client,
        draft_id,
        playbook_id=playbook["id"],
        flags=flags,
        summary=summary,
        user_id=ctx["user_id"],
    )
    record_event(
        event_type="draft",
        action="draft_checked",
        outcome="ok" if summary.get("breach", 0) == 0 else "breach",
        user_id=ctx["user_id"],
        actor_email=ctx.get("email"),
        detail=f"draft_id={draft_id} playbook_id={playbook['id']} flags={summary.get('total', 0)} breaches={summary.get('breach', 0)}",
    )
    return JSONResponse({
        "check": check,
        "playbook": {"id": playbook["id"], "name": playbook.get("name", "")},
        "flags": flags,
        "summary": summary,
    })


@router.post("/drafts/{draft_id}/export")
async def export_draft_endpoint(
    draft_id: str,
    fmt: str = "docx",
    authorization: str | None = Header(default=None),
) -> Response:
    """Export a draft as DOCX (default), Markdown or plain text."""
    ctx, user_client = _auth(authorization)
    draft = await _load_visible_draft(user_client, draft_id)

    fmt = (fmt or "docx").lower()
    safe = _safe_filename(draft.get("title") or "draft")
    if fmt == "docx":
        payload = build_docx(draft)
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        extension = "docx"
    elif fmt == "md":
        payload = build_markdown(draft).encode("utf-8")
        media_type = "text/markdown; charset=utf-8"
        extension = "md"
    elif fmt == "txt":
        payload = build_text(draft).encode("utf-8")
        media_type = "text/plain; charset=utf-8"
        extension = "txt"
    else:
        raise HTTPException(status_code=400, detail="BAD_REQUEST: fmt must be docx, md or txt")

    record_event(
        event_type="draft",
        action="draft_exported",
        outcome="ok",
        user_id=ctx["user_id"],
        actor_email=ctx.get("email"),
        detail=f"draft_id={draft_id} fmt={fmt}",
    )
    return Response(
        content=payload,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{safe}.{extension}"'},
    )

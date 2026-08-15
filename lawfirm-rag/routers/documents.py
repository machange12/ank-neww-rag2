"""Document upload, listing, and Drive ingest endpoints."""
from __future__ import annotations

import logging
import os
import tempfile
from typing import Any

from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from audit import events as audit_events
from authz import service as authz_service
from authz.models import AccessDenied
from authz.policy import classify_upload
from config import settings
from deps import ctx_rbac_from_token, read_upload_capped, require_ingest, token_from_header
from matters.lookup import matter_uuid_for, pick_user_matter, require_matter_authority, resolve_matter_ref
from search.documents_repo import list_document_intelligence, list_documents
from search.supabase_client import make_user_client

logger = logging.getLogger(__name__)
router = APIRouter(tags=["documents"])


class DriveFileIngestBody(BaseModel):
    file_id: str
    matter_id: str | None = None


def _make_ingest_classifier(
    user_client: Any,
    ctx: dict[str, Any],
    profile: Any,
    grants: list[Any],
    requested_matter: str,
    requested_access_level: int,
    administered: bool | None,
):
    """Build a per-file server-side classifier ``text -> (access_level, matter_id)``.

    Uses the same ``authz.policy.classify_upload`` rules as uploads, so bulk
    ingest is never weaker than single-file upload. Each classification is
    recorded in the audit trail.
    """
    def classify(text: str) -> tuple[int, str]:
        decision = classify_upload(
            profile,
            grants,
            text,
            requested_access_level=requested_access_level,
            requested_matter_id=requested_matter,
            administered=administered,
        )
        audit_events.log_classification(
            actor=ctx["user_id"],
            matter_id=decision.matter_id,
            access_level=decision.access_level,
            decision=decision.model_dump(),
        )
        return decision.access_level, decision.matter_id

    return classify


@router.post("/documents/upload")
async def upload_document(
    file: UploadFile = File(...),
    matter_id: str = Form(""),
    access_level: int = Form(1),
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    """
    Upload one document, classified server-side.

    SECURITY: the client-supplied ``access_level`` is a HINT only and is NEVER
    used as an authorization fact (it is recorded in the audit trail). The
    effective level is computed from the caller's DB profile (user_profiles),
    their matter grants (matter_access) and a deterministic sensitivity floor
    over the extracted text, via ``authz.policy.classify_upload``. The
    requested matter must be one the caller holds a matter_access grant for
    (unless firm_wide) and must pass the ``auth_can_administer_matter_ref`` RPC
    (firm-wide callers may write into the firm pool). The write itself goes
    through the normal document-ingest write path.

    The ``matter_id`` form field may be a matter UUID or a matter_ref string
    (e.g. "M-2024-118"); UUIDs are resolved to their matter_ref (public.matters,
    via the caller's client) before the ref-based admin RPC is called.
    """
    token = token_from_header(authorization)
    ctx, _ = ctx_rbac_from_token(token)
    user_client = make_user_client(token)

    profile = authz_service.load_profile(user_client, ctx["user_id"])
    if profile is None:
        raise HTTPException(status_code=403, detail="FORBIDDEN: no user profile found")
    grants = authz_service.load_grants(user_client, ctx["user_id"])

    requested_matter = (matter_id or "").strip()
    administered = True
    if requested_matter:
        if not profile.firm_wide:
            # The caller must hold SOME grant for the requested matter
            # (matter_access row matching the matter UUID). The administer
            # RPC below is narrower (can_administer); this guard ensures the
            # user is not ingesting into a matter they have no relationship
            # with at all.
            matter_uuid = matter_uuid_for(user_client, requested_matter)
            has_matter_grant = bool(matter_uuid) and any(
                g.matter_id == matter_uuid for g in grants
            )
            logger.info(
                "upload matter grant check: requested_matter=%r matter_uuid=%r has_grant=%s",
                requested_matter,
                matter_uuid,
                has_matter_grant,
            )
            if not has_matter_grant:
                raise HTTPException(
                    status_code=403, detail="FORBIDDEN: no matter grant authorizing ingest"
                )
        matter_ref = resolve_matter_ref(user_client, requested_matter)
        logger.info(
            "upload matter auth: requested_matter=%r resolved_matter_ref=%r", requested_matter, matter_ref
        )
        administered = authz_service.can_administer_matter_ref(user_client, matter_ref)
        logger.info(
            "upload matter auth: can_administer_matter_ref(%r) -> %s", matter_ref, administered
        )
        if not administered:
            raise HTTPException(status_code=403, detail=f"FORBIDDEN: cannot administer matter {requested_matter!r}")

    contents = await read_upload_capped(file, settings.max_upload_bytes)
    try:
        from ingest.extract import extract_text

        text, _ = extract_text(contents, file.content_type or "application/octet-stream")
    except Exception as exc:  # noqa: BLE001
        logger.warning("classification text extraction failed: %s", exc)
        text = ""

    try:
        decision = classify_upload(
            profile,
            grants,
            text,
            requested_access_level=int(access_level or 1),
            requested_matter_id=requested_matter,
            administered=administered,
        )
    except AccessDenied as exc:
        raise HTTPException(status_code=403, detail=exc.message) from exc

    audit_events.log_classification(
        actor=ctx["user_id"],
        matter_id=decision.matter_id,
        access_level=decision.access_level,
        decision=decision.model_dump(),
    )

    suffix = os.path.splitext(file.filename or "")[1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name

    try:
        from ingest.store import upsert_file

        safe_filename = file.filename or "uploaded_document"
        file_id = f"upload_{ctx['user_id']}_{safe_filename}"
        result = await upsert_file(
            file_id=file_id,
            file_title=safe_filename,
            file_url="",
            mime_type=file.content_type or "application/octet-stream",
            raw_bytes=contents,
            access_level=decision.access_level,
            matter_id=decision.matter_id,
        )
        return JSONResponse({
            "status": "ok",
            "chunks": result.get("chunks", 0),
            "file_id": file_id,
            "access_level": decision.access_level,
            "matter_id": decision.matter_id,
            "sensitivity_floor": decision.sensitivity_floor,
            "classifier": decision.classifier,
        })
    finally:
        os.unlink(tmp_path)


@router.get("/documents")
async def documents_list(
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    token = token_from_header(authorization)
    ctx_rbac_from_token(token)
    user_client = make_user_client(token)
    return JSONResponse({"documents": list_documents(user_client)})


@router.get("/documents/intelligence")
async def documents_intelligence(authorization: str | None = Header(default=None)) -> JSONResponse:
    """Return doc_type and legal_entities for all indexed documents the user can access."""
    token = token_from_header(authorization)
    ctx, _ = ctx_rbac_from_token(token)
    client = make_user_client(token)
    return JSONResponse({"documents": list_document_intelligence(client, ctx["access_level"])})


@router.get("/documents/drive-files")
async def drive_files(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    require_ingest(authorization)
    from ingest.downloader import DriveAuthError, list_folder_files

    try:
        files = list_folder_files()
    except DriveAuthError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"files": files, "total": len(files)}


@router.post("/documents/ingest-folder")
async def ingest_drive_folder(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """
    Re-ingest the entire configured Drive folder.

    SECURITY: ``access_level`` and ``matter_id`` are classified SERVER-SIDE.
    The caller's DB profile (user_profiles), matter grants (matter_access) and
    the ``auth_can_administer_matter_ref`` RPC are the only authorization facts;
    the client body and any Drive custom properties are hints only and are never
    trusted. Each file's text is classified with the same deterministic
    sensitivity floor used by ``/documents/upload`` (no weaker than uploads).
    """
    ctx, rbac = require_ingest(authorization)
    user_client = make_user_client(token_from_header(authorization))

    try:
        json_body = await request.json()
    except Exception:
        json_body = {}
    if not isinstance(json_body, dict):
        json_body = {}

    profile = authz_service.load_profile(user_client, ctx["user_id"])
    if profile is None:
        raise HTTPException(status_code=403, detail="FORBIDDEN: no user profile found")
    grants = authz_service.load_grants(user_client, ctx["user_id"])

    requested_matter = (json_body.get("matter_id") or "").strip() or await pick_user_matter(user_client, ctx)
    requested_access_level = int(json_body.get("access_level") or (rbac.get("perms") or {}).get("level") or 1)

    administered = require_matter_authority(user_client, profile, requested_matter)

    from ingest.downloader import DriveAuthError, ingest_folder

    classify = _make_ingest_classifier(
        user_client, ctx, profile, grants, requested_matter, requested_access_level, administered
    )
    try:
        return await ingest_folder(
            access_level=requested_access_level,
            matter_id=requested_matter,
            classify=classify,
        )
    except DriveAuthError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/documents/ingest-file")
async def ingest_drive_file(
    body: DriveFileIngestBody,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """
    Ingest a single Drive file, classified server-side.

    SECURITY: the effective ``access_level``/``matter_id`` come from the
    caller's DB profile/grants and the deterministic sensitivity floor over the
    file's extracted text (``authz.policy.classify_upload``), never from the
    client or from Drive custom properties. The requested matter must be
    administered by the caller (``auth_can_administer_matter_ref``).
    """
    ctx, rbac = require_ingest(authorization)
    user_client = make_user_client(token_from_header(authorization))

    profile = authz_service.load_profile(user_client, ctx["user_id"])
    if profile is None:
        raise HTTPException(status_code=403, detail="FORBIDDEN: no user profile found")
    grants = authz_service.load_grants(user_client, ctx["user_id"])

    requested_matter = (body.matter_id or "").strip() or await pick_user_matter(user_client, ctx)
    requested_access_level = int((rbac.get("perms") or {}).get("level") or 1)

    administered = require_matter_authority(user_client, profile, requested_matter)

    from ingest.downloader import DriveAuthError
    from ingest.drive_webhook import handle_drive_event

    classify = _make_ingest_classifier(
        user_client, ctx, profile, grants, requested_matter, requested_access_level, administered
    )
    try:
        return await handle_drive_event(
            file_id=body.file_id,
            event="manual",
            access_level=requested_access_level,
            matter_id=requested_matter,
            classify=classify,
        )
    except DriveAuthError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

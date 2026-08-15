"""Pure, testable authorization decision logic.

These functions take typed authorization facts (UserProfile,
MatterGrant, matter id/reference) and return decisions. They never
touch the network and never trust client-supplied values as facts.
"""
from __future__ import annotations

import re

from authz.models import AccessDenied, MatterGrant, UploadDecision, UserProfile

# Sensitivity markers (case-insensitive) -> minimum access level floor.
# Conservative by design: a higher-level marker raises the floor; a
# document is never *lowered* based on content heuristics alone.
SENSITIVITY_MARKERS: list[tuple[int, tuple[str, ...]]] = [
    (5, ("attorney-client privilege", "legally privileged", "privileged and confidential", "without prejudice")),
    (4, ("attorney work product", "work product", "highly confidential")),
    (3, ("confidential", "internal only", "restricted")),
]

PRIVILEGE_PATTERN = re.compile(
    r"(?i)(attorney-client\s*privilege|legally\s*privileged|privileged\s*and\s*confidential"
    r"|without\s*prejudice|attorney\s*work\s*product|highly\s*confidential|\bconfidential\b|\binternal\s*only\b|\brestricted\b)"
)


def sensitivity_floor(text: str) -> int:
    """Deterministic sensitivity floor for a document's text.

    Returns the highest access level implied by sensitivity markers
    found in the text (default 1 when no marker is present). This is a
    conservative baseline, never a claim of perfect classification.
    """
    if not text:
        return 1
    floor = 1
    for level, markers in SENSITIVITY_MARKERS:
        lowered = text.lower()
        if any(marker in lowered for marker in markers):
            floor = max(floor, level)
    return floor


def is_admin(profile: UserProfile) -> bool:
    """Explicit admin permission. An access level alone is never admin."""
    return profile.is_admin()


def requires_admin(profile: UserProfile) -> None:
    if not is_admin(profile):
        raise AccessDenied(
            f"FORBIDDEN: user {profile.user_id} is not an admin (explicit admin flag required)"
        )


def can_administer_any_matter(grants: list[MatterGrant], profile: UserProfile) -> bool:
    if profile.firm_wide:
        return True
    return any(g.can_administer for g in grants)


def effective_read_ceiling(profile: UserProfile, grants: list[MatterGrant], matter_ref: str | None) -> int:
    """Max access level the caller may read in the given scope."""
    ceiling = profile.access_level
    if not profile.firm_wide and matter_ref:
        matter_ceiling = max(
            (g.access_level for g in grants if g.matter_id and g.matter_id == matter_ref),
            default=0,
        )
        ceiling = min(ceiling, matter_ceiling) if matter_ceiling else ceiling
    return ceiling


def classify_upload(
    profile: UserProfile,
    grants: list[MatterGrant],
    content_text: str,
    requested_access_level: int | None = None,
    requested_matter_id: str | None = None,
    administered: bool | None = None,
) -> UploadDecision:
    """Server-side classification of an upload/ingest.

    Rules (documented in docs/architecture/foundation-integrity.md):
      1. The client-supplied ``access_level`` is a HINT only and is
         never used as an authorization fact. The final level is
         computed server-side.
      2. ceiling  = the caller's authority (profile ceiling, capped
         by the matter admin level when matter-scoped and not
         firm-wide).
      3. floor    = deterministic sensitivity floor from content
         markers.
      4. final access_level = min(ceiling, floor).
         * a document cannot be marked MORE sensitive than the caller
           may handle (capped at ceiling);
         * a document containing sensitivity markers cannot be stamped
           lower than the floor (cannot make a sensitive file broadly
           accessible).
      5. matter_id must be '' (firm-wide, only if the caller has
         firm-wide authority) or a matter the caller can administer.
      6. If the caller cannot administer any matter and is not
         firm-wide, upload is denied.
      7. ``administered`` (bool) is the authoritative per-matter
         verdict from the auth_can_administer_matter_ref RPC when
         available. When False, the requested matter is not
         administered by the caller and ingest is denied regardless of
         grants for other matters.
    """
    floor = sensitivity_floor(content_text)
    ceiling = profile.access_level

    requested_matter = (requested_matter_id or "").strip()
    if requested_matter:
        if not profile.firm_wide:
            if administered is False:
                raise AccessDenied(
                    "FORBIDDEN: caller cannot administer the requested matter"
                )
            # Matter-scoped ingest requires an administer grant on that matter.
            matter_admin_ceiling = max(
                (g.access_level for g in grants if g.can_administer and g.matter_id),
                default=0,
            )
            if matter_admin_ceiling < 1:
                raise AccessDenied("FORBIDDEN: no matter grant authorizing ingest")
            ceiling = min(ceiling, matter_admin_ceiling)
        # firm_wide callers keep their profile ceiling for any matter they administer.
    else:
        if not profile.firm_wide:
            raise AccessDenied("FORBIDDEN: only firm-wide administrators may ingest into the firm pool")

    effective = min(ceiling, floor)
    return UploadDecision(
        access_level=effective,
        matter_id=requested_matter,
        firm_wide=not bool(requested_matter),
        requested_access_level=requested_access_level,
        requested_matter_id=requested_matter_id,
        sensitivity_floor=floor,
        ceiling=ceiling,
        classifier="deterministic_sensitivity",
        reason=(
            f"floor={floor} ceiling={ceiling} effective={effective}"
            + (" (firm-wide)" if not requested_matter else "")
        ),
    )
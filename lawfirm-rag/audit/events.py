"""Security-event logging.

Events are written to public.security_events through the service-role
client. This is a narrowly scoped BACKEND WRITE path (audit trail), not
a read path and not the normal retrieval path; it is acceptable and
documented in docs/security/threat-model.md.

Failure to persist an audit event never breaks the originating request.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _service_client() -> Any:
    from search.service_client import make_service_client

    return make_service_client()


def record_event(
    *,
    event_type: str,
    action: str,
    outcome: str,
    user_id: str | None = None,
    actor_email: str | None = None,
    detail: str | None = None,
    ip_address: str | None = None,
) -> None:
    """Persist a security event. Never raises (best-effort audit trail)."""
    try:
        row = {
            "event_type": event_type,
            "action": action,
            "outcome": outcome,
            "user_id": user_id,
            "actor_email": actor_email,
            "detail": (detail or "")[:4000],
            "ip_address": ip_address,
        }
        _service_client().table("security_events").insert(row).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("audit event not persisted (%s/%s): %s", event_type, outcome, exc)


def log_denial(*, action: str, reason: str, user_id: str | None = None, actor_email: str | None = None, ip_address: str | None = None) -> None:
    record_event(
        event_type="access_denied",
        action=action,
        outcome="denied",
        user_id=user_id,
        actor_email=actor_email,
        detail=reason,
        ip_address=ip_address,
    )


def log_suspicious(*, action: str, reason: str, user_id: str | None = None, actor_email: str | None = None, ip_address: str | None = None) -> None:
    record_event(
        event_type="suspicious",
        action=action,
        outcome="flagged",
        user_id=user_id,
        actor_email=actor_email,
        detail=reason,
        ip_address=ip_address,
    )


def log_classification(*, actor: str, matter_id: str, access_level: int, decision: dict[str, Any]) -> None:
    """Record a server-side upload classification (classifier, actor, timestamp)."""
    record_event(
        event_type="classification",
        action="upload_classified",
        outcome="ok",
        user_id=actor,
        detail=f"matter={matter_id!r} access_level={access_level} decision={decision}",
    )


def log_rate_limit(*, user_id: str | None, ip_address: str | None = None) -> None:
    record_event(
        event_type="rate_limit",
        action="chat_request",
        outcome="denied",
        user_id=user_id,
        ip_address=ip_address,
    )
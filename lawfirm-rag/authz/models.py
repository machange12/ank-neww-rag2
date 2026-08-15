"""Typed models for authorization facts loaded from the database."""
from __future__ import annotations

from pydantic import BaseModel


class UserProfile(BaseModel):
    """DB source of truth for a user's authorization facts."""

    user_id: str | None = None
    tenant_id: str | None = None
    role: str = "associate"
    access_level: int = 1
    firm_wide: bool = False
    admin: bool = False

    def is_admin(self) -> bool:
        """Explicit admin flag only. An access level is never administration."""
        return bool(self.admin)


class MatterGrant(BaseModel):
    """One row of matter_access."""

    matter_id: str | None = None  # uuid; None = firm-wide grant
    access_level: int = 1
    can_administer: bool = False


class UploadDecision(BaseModel):
    """Server-side classification result for an upload/ingest."""

    access_level: int
    matter_id: str
    firm_wide: bool
    requested_access_level: int | None = None
    requested_matter_id: str | None = None
    sensitivity_floor: int
    ceiling: int
    classifier: str = "deterministic_sensitivity"
    reason: str = ""


class AccessDenied(Exception):
    """Raised when an operation is not permitted for the caller."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
from __future__ import annotations

from typing import TypedDict


class RolePerms(TypedDict):
    level: int
    view_all: bool
    privileged: bool
    ingest: bool
    admin: bool


ROLE_MATRIX: dict[str, RolePerms] = {
    "managing_partner": {"level": 5, "view_all": True,  "privileged": True,  "ingest": True,  "admin": True},
    "partner":          {"level": 4, "view_all": False, "privileged": True,  "ingest": True,  "admin": False},
    "senior_associate": {"level": 3, "view_all": False, "privileged": False, "ingest": True,  "admin": False},
    "associate":        {"level": 2, "view_all": False, "privileged": False, "ingest": False, "admin": False},
    "paralegal":        {"level": 2, "view_all": False, "privileged": False, "ingest": False, "admin": False},
    "legal_secretary":  {"level": 1, "view_all": False, "privileged": False, "ingest": False, "admin": False},
    "it_admin":         {"level": 5, "view_all": True,  "privileged": False, "ingest": True,  "admin": True},
}

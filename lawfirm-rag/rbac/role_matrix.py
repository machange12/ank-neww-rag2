from __future__ import annotations

from dataclasses import dataclass


ROLE_MATRIX: dict[str, dict[str, object]] = {
    "managing_partner": {"level": 5, "view_all": True,  "privileged": True,  "ingest": True,  "admin": True},
    "partner":          {"level": 4, "view_all": False, "privileged": True,  "ingest": True,  "admin": False},
    "senior_associate": {"level": 3, "view_all": False, "privileged": False, "ingest": True,  "admin": False},
    "associate":        {"level": 2, "view_all": False, "privileged": False, "ingest": False, "admin": False},
    "paralegal":        {"level": 2, "view_all": False, "privileged": False, "ingest": False, "admin": False},
    "legal_secretary":  {"level": 1, "view_all": False, "privileged": False, "ingest": False, "admin": False},
    "it_admin":         {"level": 5, "view_all": True,  "privileged": False, "ingest": True,  "admin": True},
}


@dataclass(frozen=True)
class RolePermissions:
    role: str
    level: int
    view_all: bool
    privileged: bool
    ingest: bool
    admin: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "role": self.role,
            "level": self.level,
            "view_all": self.view_all,
            "privileged": self.privileged,
            "ingest": self.ingest,
            "admin": self.admin,
        }


def get_permissions(role: str) -> RolePermissions:
    raw = ROLE_MATRIX.get(role)
    if not raw:
        raise PermissionError(f'Unrecognized role "{role}"')
    return RolePermissions(
        role=role,
        level=int(raw["level"]),
        view_all=bool(raw["view_all"]),
        privileged=bool(raw["privileged"]),
        ingest=bool(raw["ingest"]),
        admin=bool(raw["admin"]),
    )

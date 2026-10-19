"""Shared test fixtures for the lawfirm-rag test suite.

All fixtures are local and deterministic. No live database, no secrets, no
external network calls. Tests that need a Supabase client use a fake client
with a small in-memory store so RLS-driven ownership logic can be exercised
without a real database.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

# Ensure the lawfirm-rag project root is importable regardless of how pytest
# derives rootdir (tests run from the repo root via `python -m pytest tests/`).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


class FakeTable:
    """Minimal PostgREST-like builder with an in-memory row store.

    ``owner_column``/``owner_value`` simulate RLS: when set, ``execute()``
    filters rows to those where ``row[owner_column] == owner_value``.
    """

    def __init__(
        self,
        rows: list[dict[str, Any]],
        owner_column: str | None = None,
        owner_value: Any = None,
        matter_grants: set[str] | None = None,
        defaults: dict[str, Any] | None = None,
    ) -> None:
        self.rows = rows
        self._op: dict[str, Any] = {}
        self._owner_column = owner_column
        self._owner_value = owner_value
        # Simulates migration 20261013000014's READ-ONLY matter sharing: a row
        # with visibility='matter' and a matter_id the caller holds a grant for
        # is visible on SELECT (never on update/delete, which stay owner-only).
        self._matter_grants = matter_grants or set()
        # Column defaults the live database would apply on insert.
        self._defaults = defaults or {}
        self._pending_insert: list[dict[str, Any]] | None = None

    def select(self, cols: str):
        self._op["cols"] = cols
        return self

    def eq(self, col: str, value: Any):
        self._op.setdefault("filters", []).append(("eq", col, value))
        return self

    def in_(self, col: str, values: list[Any]):
        self._op.setdefault("filters", []).append(("in", col, values))
        return self

    def limit(self, n: int):
        self._op["limit"] = n
        return self

    def order(self, col: str, desc: bool = False):
        self._op["order"] = (col, desc)
        return self

    def insert(self, rows):
        # Mirrors the real client: insert() returns the builder, execute() runs it.
        if not isinstance(rows, list):
            rows = [rows]
        inserted = []
        for r in rows:
            row = dict(r)
            for key, value in self._defaults.items():
                row.setdefault(key, value)
            row.setdefault("id", f"id-{len(self.rows) + len(inserted) + 1}")
            self.rows.append(row)
            inserted.append(row)
        self._pending_insert = inserted
        return self

    def delete(self):
        self._op["delete"] = True
        return self

    def update(self, patch: dict[str, Any]):
        self._op["update"] = dict(patch)
        return self

    def execute(self):
        if self._pending_insert is not None:
            result, self._pending_insert = self._pending_insert, None
            return _FakeResponse(result)
        rows = list(self.rows)
        if self._owner_column is not None and self._owner_value is not None:
            # Writes stay owner-only; only SELECT sees matter-shared rows.
            read_only = not self._op.get("delete") and self._op.get("update") is None
            rows = [
                r
                for r in rows
                if r.get(self._owner_column) == self._owner_value
                or (
                    read_only
                    and self._matter_grants
                    and r.get("visibility") == "matter"
                    and (r.get("matter_id") or "") in self._matter_grants
                )
            ]
        for op, col, value in self._op.get("filters", []):
            if op == "eq":
                rows = [r for r in rows if r.get(col) == value]
            elif op == "in":
                rows = [r for r in rows if r.get(col) in value]
        order = self._op.get("order")
        if order:
            col, desc = order
            # (is-None, value) key: keeps Nones last and never compares across
            # types (e.g. an int order_index column) the way `value or ""` did.
            rows = sorted(rows, key=lambda r: (r.get(col) is None, r.get(col)), reverse=desc)
        limit = self._op.get("limit")
        if limit:
            rows = rows[:limit]
        update = self._op.get("update")
        if update is not None:
            for r in rows:
                r.update(update)
            return _FakeResponse(rows)
        if self._op.get("delete"):
            self.rows[:] = [r for r in self.rows if r not in rows]
            return _FakeResponse([])
        return _FakeResponse(rows)


class _FakeResponse:
    def __init__(self, data: list[Any]) -> None:
        self.data = data


# Tables whose RLS key is the owning user (user_id_uuid / user_id).
_OWNER_TABLE_COLUMNS = {
    "chat_sessions": "user_id_uuid",
    "chat_memory": "user_id",
    "query_feedback": "user_id_uuid",
    "user_profiles": "user_id",
    "matter_access": "user_id",
    "drafts": "user_id_uuid",
}

# Column defaults the live schema applies on insert (migration 20261013000014:
# drafts.visibility defaults to 'private').
_TABLE_DEFAULTS = {
    "drafts": {"visibility": "private"},
}


class FakeClient:
    """Fake Supabase client backed by per-table row stores + RPC responses.

    When ``auth_uid`` is set, owned tables behave like the real RLS policies
    (the caller only sees/inserts their own rows).
    """

    def __init__(
        self,
        tables: dict[str, list[dict[str, Any]]] | None = None,
        rpcs: dict[str, Any] | None = None,
        auth_uid: str | None = None,
        matter_grants: set[str] | None = None,
    ) -> None:
        self._tables = {name: list(rows) for name, rows in (tables or {}).items()}
        self._rpcs = dict(rpcs or {})
        self._current_rpc: tuple[str, dict] | None = None
        self.auth_uid = auth_uid
        self.matter_grants = set(matter_grants or [])
        self.token: str | None = None

    def table(self, name: str) -> FakeTable:
        owner_col = _OWNER_TABLE_COLUMNS.get(name)
        owner_value = self.auth_uid if owner_col else None
        return FakeTable(
            self._tables.setdefault(name, []),
            owner_column=owner_col,
            owner_value=owner_value,
            matter_grants=self.matter_grants if name == "drafts" else None,
            defaults=_TABLE_DEFAULTS.get(name),
        )

    def rpc(self, name: str, params: dict[str, Any]):
        self._current_rpc = (name, params)
        return self

    def execute(self):
        name, params = self._current_rpc or ("", {})
        handler = self._rpcs.get(name)
        if handler is None:
            raise RuntimeError(f"no RPC stub for {name!r}")
        return _FakeResponse(handler(params))


@pytest.fixture
def fake_client() -> FakeClient:
    return FakeClient()


@pytest.fixture
def chat_tables():
    """chat_sessions / chat_memory row stores for ownership tests."""
    return {
        "chat_sessions": [
            {
                "session_id": "own-session-1",
                "user_id": "user-1",
                "user_id_uuid": "user-1",
                "tenant_id": "00000000-0000-0000-0000-000000000001",
            },
            {
                "session_id": "other-session-1",
                "user_id": "user-2",
                "user_id_uuid": "user-2",
                "tenant_id": "00000000-0000-0000-0000-000000000001",
            },
        ],
        "chat_memory": [
            {
                "session_id": "own-session-1",
                "user_id": "user-1",
                "message": {"type": "human", "data": {"content": "First question about the lease."}},
            },
            {
                "session_id": "own-session-1",
                "user_id": "user-1",
                "message": {"type": "ai", "data": {"content": "An answer."}},
            },
        ],
    }
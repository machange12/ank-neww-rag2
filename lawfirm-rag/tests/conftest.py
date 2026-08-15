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
    ) -> None:
        self.rows = rows
        self._op: dict[str, Any] = {}
        self._owner_column = owner_column
        self._owner_value = owner_value
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
            row.setdefault("id", f"id-{len(self.rows) + len(inserted) + 1}")
            self.rows.append(row)
            inserted.append(row)
        self._pending_insert = inserted
        return self

    def delete(self):
        self._op["delete"] = True
        return self

    def execute(self):
        if self._pending_insert is not None:
            result, self._pending_insert = self._pending_insert, None
            return _FakeResponse(result)
        rows = list(self.rows)
        if self._owner_column and self._owner_value is not None:
            rows = [r for r in rows if r.get(self._owner_column) == self._owner_value]
        for op, col, value in self._op.get("filters", []):
            if op == "eq":
                rows = [r for r in rows if r.get(col) == value]
            elif op == "in":
                rows = [r for r in rows if r.get(col) in value]
        order = self._op.get("order")
        if order:
            col, desc = order
            rows = sorted(rows, key=lambda r: (r.get(col) or ""), reverse=desc)
        limit = self._op.get("limit")
        if limit:
            rows = rows[:limit]
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
    ) -> None:
        self._tables = {name: list(rows) for name, rows in (tables or {}).items()}
        self._rpcs = dict(rpcs or {})
        self._current_rpc: tuple[str, dict] | None = None
        self.auth_uid = auth_uid
        self.token: str | None = None

    def table(self, name: str) -> FakeTable:
        owner_col = _OWNER_TABLE_COLUMNS.get(name)
        owner_value = self.auth_uid if owner_col else None
        return FakeTable(
            self._tables.setdefault(name, []),
            owner_column=owner_col,
            owner_value=owner_value,
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
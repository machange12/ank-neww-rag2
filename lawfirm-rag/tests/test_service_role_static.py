"""Static security check: the service-role client must not leak into
user-facing retrieval/history read paths.

Files that MUST NOT import or reference the service-role client:
  * app.py, deps.py
  * routers/** (auth.py, chat.py, documents.py, admin.py — admin.py reaches
    the service role only indirectly via authz.service.admin_client(), which
    is the documented exception below)
  * matters/lookup.py, search/documents_repo.py
  * agents/rag_agent.py
  * sessions/** (manager.py, supabase_history.py)
  * search/hybrid.py

The service-role client is allowed ONLY in backend write/worker paths:
ingest/*, cleanup/*, audit/events.py (audit trail), authz/service.py
(admin-management write path).
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

FORBIDDEN_FILES = [
    PROJECT_ROOT / "app.py",
    PROJECT_ROOT / "deps.py",
    PROJECT_ROOT / "routers" / "auth.py",
    PROJECT_ROOT / "routers" / "chat.py",
    PROJECT_ROOT / "routers" / "documents.py",
    PROJECT_ROOT / "routers" / "admin.py",
    PROJECT_ROOT / "matters" / "lookup.py",
    PROJECT_ROOT / "search" / "documents_repo.py",
    PROJECT_ROOT / "agents" / "rag_agent.py",
    PROJECT_ROOT / "sessions" / "manager.py",
    PROJECT_ROOT / "sessions" / "supabase_history.py",
    PROJECT_ROOT / "search" / "hybrid.py",
]

FORBIDDEN_PATTERNS = [
    r"make_service_client",
    r"service_client",
    r"service_role",
    r"supabase_service_role_key",
]


def _find_service_role_refs(text: str) -> list[str]:
    hits = []
    for pattern in FORBIDDEN_PATTERNS:
        hits.extend(re.findall(pattern, text))
    return hits


def test_service_role_not_in_forbidden_paths():
    for path in FORBIDDEN_FILES:
        assert path.exists(), f"expected file {path} exists"
        text = path.read_text(encoding="utf-8")
        hits = _find_service_role_refs(text)
        assert not hits, f"{path.relative_to(PROJECT_ROOT)} contains forbidden service-role refs: {hits}"


def test_ast_no_service_client_import_in_app():
    """AST-level check for app.py imports specifically."""
    tree = ast.parse((PROJECT_ROOT / "app.py").read_text(encoding="utf-8"))
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
    service_imports = [m for m in imports if "service_client" in m or "service_role" in m]
    assert not service_imports, f"app.py imports service-role client: {service_imports}"


def test_rag_agent_uses_user_client_for_history():
    """rag_agent must build SupabaseChatHistory with the passed user client."""
    text = (PROJECT_ROOT / "agents" / "rag_agent.py").read_text(encoding="utf-8")
    assert "client=user_client" in text
    assert "user_id=" in text
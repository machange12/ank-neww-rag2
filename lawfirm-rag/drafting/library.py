"""Curated drafting-library loader (templates, clause bank, playbooks).

This is a BACKEND CURATION WRITE PATH — it runs with the service-role client,
exactly like ``ingest/*``. It is not reachable from any user-facing request and
never reads user content. The library tables are read-only to authenticated
users (see migration 20261013000012 RLS policies).

Idempotent: re-seeding removes prior rows with the same natural key
(title/doc_type for clauses & templates, name for playbooks) before inserting,
so a corrected seed file replaces content rather than duplicating it.
"""
from __future__ import annotations

import logging
from typing import Any

from postgrest_utils import response_data
from providers import embed_prefixes, get_bulk_embeddings

logger = logging.getLogger(__name__)


def _clause_embed_text(clause: dict[str, Any]) -> str:
    """Embedding input for a clause — mirrors ingest/embed.py::enrich_chunk's
    context prefix so query-side and document-side prefixes stay in sync."""
    _, doc_prefix = embed_prefixes()
    return (
        f"{doc_prefix}Clause: {clause.get('title', '')}\n"
        f"Type: {clause.get('clause_type', '')}\n\n{clause.get('body', '')}"
    )


def _delete_existing(client: Any, table: str, column: str, value: Any) -> None:
    try:
        client.table(table).delete().eq(column, value).execute()
    except Exception as exc:  # noqa: BLE001
        logger.debug("library delete before seed failed (%s.%s=%r): %s", table, column, value, exc)


def seed_templates(client: Any, templates: list[dict[str, Any]], tenant_id: str | None) -> int:
    count = 0
    for tpl in templates:
        _delete_existing(client, "draft_templates", "title", tpl["title"])
        row = {
            "tenant_id": tenant_id,
            "doc_type": tpl.get("doc_type", ""),
            "practice_area": tpl.get("practice_area", ""),
            "jurisdiction": tpl.get("jurisdiction", "ke"),
            "title": tpl["title"],
            "description": tpl.get("description", ""),
            "skeleton": tpl.get("skeleton", []),
            "access_level": int(tpl.get("access_level", 1)),
            "matter_id": tpl.get("matter_id", ""),
        }
        client.table("draft_templates").insert(row).execute()
        count += 1
    return count


def seed_clauses(client: Any, clauses: list[dict[str, Any]], tenant_id: str | None) -> int:
    if not clauses:
        return 0
    embedder = get_bulk_embeddings()
    embeddings = embedder.embed_documents([_clause_embed_text(c) for c in clauses])
    count = 0
    for clause, embedding in zip(clauses, embeddings):
        _delete_existing(client, "draft_clauses", "title", clause["title"])
        row = {
            "tenant_id": tenant_id,
            "doc_type": clause.get("doc_type", ""),
            "practice_area": clause.get("practice_area", ""),
            "clause_type": clause.get("clause_type", ""),
            "title": clause["title"],
            "body": clause["body"],
            "is_standard": bool(clause.get("is_standard", True)),
            "access_level": int(clause.get("access_level", 1)),
            "matter_id": clause.get("matter_id", ""),
            "source_file_id": clause.get("source_file_id"),
            "embedding": list(embedding),
        }
        client.table("draft_clauses").insert(row).execute()
        count += 1
    return count


def seed_playbooks(client: Any, playbooks: list[dict[str, Any]], tenant_id: str | None) -> int:
    count = 0
    for pb in playbooks:
        _delete_existing(client, "playbooks", "name", pb["name"])
        row = {
            "tenant_id": tenant_id,
            "name": pb["name"],
            "doc_type": pb.get("doc_type", ""),
            "description": pb.get("description", ""),
            "access_level": int(pb.get("access_level", 1)),
            "matter_id": pb.get("matter_id", ""),
        }
        res = client.table("playbooks").insert(row).execute()
        data = response_data(res)
        playbook_id = data[0]["id"] if isinstance(data, list) and data else None
        if not playbook_id:
            continue
        for index, rule in enumerate(pb.get("rules", [])):
            client.table("playbook_rules").insert({
                "playbook_id": playbook_id,
                "order_index": index,
                "clause_type": rule.get("clause_type", ""),
                "position": rule.get("position", "advisory"),
                "rule_text": rule["rule_text"],
                "severity": rule.get("severity", "medium"),
            }).execute()
        count += 1
    return count


def seed_library(
    client: Any,
    data: dict[str, Any],
    tenant_id: str | None = None,
) -> dict[str, int]:
    """Seed templates + clauses + playbooks from a parsed seed document."""
    summary = {
        "templates": seed_templates(client, data.get("templates", []), tenant_id),
        "clauses": seed_clauses(client, data.get("clauses", []), tenant_id),
        "playbooks": seed_playbooks(client, data.get("playbooks", []), tenant_id),
    }
    logger.info("drafting library seeded: %s", summary)
    return summary

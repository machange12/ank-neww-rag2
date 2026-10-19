"""Per-account document privacy (migration 20261013000016).

Two layers are tested here:
  * the write path must stamp ownership, because ingest writes through the
    service role and therefore bypasses RLS entirely (a policy can never
    enforce what was never recorded);
  * the read path must not leak another account's upload through a shared
    draft's citation list.

The SQL policy/RPC predicate itself is asserted statically (offline), since the
suite never touches a live database — see tests/test_schema_manifest.py for the
manifest check and docs/security for the threat model.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import ingest.store as store
from drafting.store import RESTRICTED_SOURCE_LABEL, _redact_unreadable_sources
from ingest.embed import TextChunk
from ingest.extract import ExtractedDocument
from routers.documents import _share_rpc_error
from tests.conftest import FakeClient

MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "supabase" / "migrations" / "20261013000016_document_ownership.sql"
)


# ---------------------------------------------------------------------------
# Write path: ownership must be stamped
# ---------------------------------------------------------------------------

def test_private_scope_requires_an_owner():
    with pytest.raises(ValueError, match="owner_user_id"):
        asyncio.run(
            store.upsert_file(
                file_id="upload_x_contract.pdf",
                file_title="contract.pdf",
                file_url="",
                mime_type="application/pdf",
                raw_bytes=b"%PDF",
                access_level=1,
                matter_id="",
                owner_user_id=None,
                doc_scope="private",
            )
        )


def test_unknown_scope_is_rejected():
    with pytest.raises(ValueError, match="doc_scope"):
        asyncio.run(
            store.upsert_file(
                file_id="f1",
                file_title="t",
                file_url="",
                mime_type="application/pdf",
                raw_bytes=b"%PDF",
                access_level=1,
                matter_id="",
                doc_scope="shared",
            )
        )


def _patch_ingest_pipeline(monkeypatch, client) -> None:
    """Stub everything downstream of the ownership decision."""
    monkeypatch.setattr(store, "make_service_client", lambda: client)
    monkeypatch.setattr(store, "make_chat_llm", lambda: object())
    monkeypatch.setattr(store, "classify_document", lambda text, title, llm: {"doc_type": "contract"})
    monkeypatch.setattr(store, "extract_legal_entities", lambda text, doc_type, llm: {})
    monkeypatch.setattr(
        store,
        "extract_document",
        lambda raw, mime: ExtractedDocument(text="hello world", total_pages=1, segments=(), locator_kind="none"),
    )
    monkeypatch.setattr(
        store,
        "chunk_text_auto",
        lambda text, llm=None, segments=None, locator_kind="none": (
            [TextChunk(text="hello world", section_heading="", chunk_index=0)],
            "recursive",
        ),
    )
    monkeypatch.setattr(store, "embed_chunks", lambda chunks, file_title="": [[0.1] * 768])
    monkeypatch.setattr(store, "total_tokens", lambda chunks: 3)


def _ingest(monkeypatch, **kwargs) -> FakeClient:
    client = FakeClient(rpcs={"delete_documents_by_file_id_before": lambda params: None})
    _patch_ingest_pipeline(monkeypatch, client)
    defaults = dict(
        file_id="upload_11111111-1111-1111-1111-111111111111_contract.pdf",
        file_title="contract.pdf",
        file_url="",
        mime_type="application/pdf",
        raw_bytes=b"%PDF-1.4",
        access_level=2,
        matter_id="",
        drive_modified_time="",
    )
    defaults.update(kwargs)
    asyncio.run(store.upsert_file(**defaults))
    return client


def test_upload_ingest_stamps_owner_and_private_scope(monkeypatch):
    owner = "11111111-1111-1111-1111-111111111111"
    client = _ingest(monkeypatch, owner_user_id=owner, doc_scope="private")

    chunks = client._tables["documents"]
    assert chunks, "expected chunk rows to be written"
    assert all(row["owner_user_id"] == owner for row in chunks)
    assert all(row["doc_scope"] == "private" for row in chunks)

    meta = client._tables["document_metadata"]
    assert len(meta) == 1
    assert meta[0]["owner_user_id"] == owner
    assert meta[0]["doc_scope"] == "private"


def test_corpus_ingest_leaves_owner_empty(monkeypatch):
    client = _ingest(monkeypatch, file_id="drive-file-123")

    assert all(row["owner_user_id"] is None for row in client._tables["documents"])
    assert all(row["doc_scope"] == "corpus" for row in client._tables["documents"])
    assert client._tables["document_metadata"][0]["doc_scope"] == "corpus"


# ---------------------------------------------------------------------------
# Read path: a shared draft must not leak an unreadable source
# ---------------------------------------------------------------------------

def _section(sources):
    return {"order_index": 0, "heading": "Background", "sources": sources}


def test_redaction_strips_sources_the_reader_cannot_see():
    reader = FakeClient(tables={"document_metadata": [{"file_id": "corpus-1"}]})
    sections = [
        _section([
            {
                "n": 1,
                "file_id": "corpus-1",
                "file_title": "Shared precedent.pdf",
                "quote": "the firm corpus clause",
                "label": "p. 3",
                "url": "https://drive/x",
            },
            {
                "n": 2,
                "file_id": "upload_other_user_private.pdf",
                "file_title": "Board minutes.pdf",
                "quote": "confidential settlement strategy",
                "label": "pp. 12–13",
                "url": "",
                "locations": [{"page_start": 12}],
            },
        ])
    ]

    asyncio.run(_redact_unreadable_sources(reader, sections))

    kept, redacted = sections[0]["sources"]
    # The readable source is untouched.
    assert kept["file_title"] == "Shared precedent.pdf"
    assert kept["quote"] == "the firm corpus clause"
    # The unreadable one keeps only its citation number.
    assert redacted["n"] == 2
    assert redacted["file_title"] == RESTRICTED_SOURCE_LABEL
    assert redacted["title"] == RESTRICTED_SOURCE_LABEL
    assert redacted["restricted"] is True
    assert redacted["file_id"] == ""
    for key in ("quote", "label", "url", "locations", "page_start"):
        assert key not in redacted, f"{key} leaked from an unreadable source"


def test_redaction_leaves_clause_sources_alone():
    # Clause-bank sources carry no file_id (they are level-gated by their own
    # RLS), so they must not be touched.
    reader = FakeClient(tables={"document_metadata": []})
    sections = [_section([{"n": 1, "kind": "clause", "title": "Force majeure clause", "quote": "…"}])]

    asyncio.run(_redact_unreadable_sources(reader, sections))

    assert sections[0]["sources"][0]["title"] == "Force majeure clause"


def test_redaction_fails_closed_when_the_lookup_errors():
    class Boom:
        def table(self, _name):
            raise RuntimeError("connection lost")

    sections = [_section([{"n": 1, "file_id": "corpus-1", "title": "x", "quote": "secret"}])]

    asyncio.run(_redact_unreadable_sources(Boom(), sections))

    source = sections[0]["sources"][0]
    assert "quote" not in source
    assert source["title"] == RESTRICTED_SOURCE_LABEL


# ---------------------------------------------------------------------------
# Error translation for the share RPCs
# ---------------------------------------------------------------------------

def test_share_error_mapping():
    class Err(Exception):
        def __init__(self, message):
            super().__init__(message)
            self.message = message

    assert _share_rpc_error(Err("forbidden: only the owner can share this document"), "x").status_code == 403
    assert _share_rpc_error(Err("not_found: no user with email a@b.c"), "x").status_code == 404
    assert _share_rpc_error(Err("invalid: you cannot share a document with yourself"), "x").status_code == 400
    assert _share_rpc_error(Err("some other failure"), "x").status_code == 400


# ---------------------------------------------------------------------------
# Static assertions on the migration (offline; no live database)
# ---------------------------------------------------------------------------

def test_migration_is_registered_and_defines_the_ownership_predicate():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "20261013000016" in sql
    # Fail-closed guard: a private row must always carry an owner.
    assert "doc_scope <> 'private' or owner_user_id is not null" in sql
    # The predicate is applied in both retrieval RPCs and both SELECT policies.
    assert sql.count("d.doc_scope = 'corpus'") == 3  # match_documents_rls + 2 CTEs
    assert sql.count("doc_scope = 'corpus'") >= 5

    # Every private branch must test the owner and the explicit share.
    for branch in ("owner_user_id = auth.uid()", "auth_document_shared_with_me("):
        assert branch in sql

    # The share INSERT policy must require ownership — otherwise any user could
    # grant themselves a share row for somebody else's file.
    assert "and public.auth_owns_document(file_id)" in sql

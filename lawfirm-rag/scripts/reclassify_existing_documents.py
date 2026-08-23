"""
reclassify_existing_documents.py
==================================
One-off backfill: re-runs ingest/classifier.py's classify_document() +
extract_legal_entities() against every already-ingested file and corrects
the doc_type/jurisdiction/language/legal_entities that were silently wrong.

Root cause (fixed separately in ingest/classifier.py + ingest/store.py):
classification previously hardcoded a ChatOpenAI client using
settings.openai_api_key, but this deployment only configures GROQ_API_KEY —
so every classification call failed auth, was caught by the broad except,
and silently defaulted to doc_type="unknown" for every file ever ingested.
That "unknown" tag then broke retrieval for any query that happened to
trigger agents/rag_agent.py's keyword-based doc_type_hint (e.g. a query
containing "case" sets doc_type_hint="judgment", which the hybrid RPC then
exact-matches against metadata->>'doc_type' — "unknown" != "judgment", so
real judgments were silently filtered out of every such query).

This script updates BOTH:
  - document_metadata.doc_type / legal_entities (per-file row)
  - documents.metadata->>'doc_type' / 'jurisdiction' / 'doc_language' /
    'legal_entities' on EVERY chunk of that file (retrieval filters on the
    chunk-level metadata, not the per-file table, so the per-file fix alone
    would not have fixed retrieval)

Source text for classification is reconstructed by concatenating each
file's existing chunks (ordered by chunk_index) rather than re-downloading
from Drive/re-extracting — consistent with the same approach used in
scripts/experiment_chunk_sizes.py.

Usage:
    python scripts/reclassify_existing_documents.py            # all files
    python scripts/reclassify_existing_documents.py --file-id <file_id>
    python scripts/reclassify_existing_documents.py --dry-run  # classify + print only, no writes
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingest.classifier import classify_document, extract_legal_entities
from postgrest_utils import response_data
from providers import make_chat_llm
from search.service_client import make_service_client


def _list_file_ids(client: Any) -> list[str]:
    resp = client.table("document_metadata").select("file_id").execute()
    rows = response_data(resp) or []
    return [r["file_id"] for r in rows]


def _reconstruct_text_and_chunks(client: Any, file_id: str) -> tuple[str, list[dict[str, Any]]]:
    resp = (
        client.table("documents")
        .select("id, content, metadata")
        .filter("metadata->>file_id", "eq", file_id)
        .execute()
    )
    rows = response_data(resp) or []
    rows.sort(key=lambda r: (r.get("metadata") or {}).get("chunk_index", 0) or 0)
    text = "\n\n".join(r.get("content") or "" for r in rows)
    return text, rows


def _reclassify_one(client: Any, llm: Any, file_id: str, dry_run: bool) -> None:
    metadata_resp = (
        client.table("document_metadata").select("file_id, file_title").eq("file_id", file_id).execute()
    )
    meta_rows = response_data(metadata_resp) or []
    if not meta_rows:
        print(f"  [skip] {file_id}: no document_metadata row")
        return
    file_title = meta_rows[0].get("file_title", "")

    text, chunk_rows = _reconstruct_text_and_chunks(client, file_id)
    if not text:
        print(f"  [skip] {file_id}: no chunks found to reconstruct source text")
        return

    doc_classification = classify_document(text, file_title, llm)
    doc_type = doc_classification.get("doc_type", "unknown")
    legal_entities_raw = extract_legal_entities(text, doc_type, llm)
    doc_intelligence = {**doc_classification, **legal_entities_raw}

    print(
        f"  {file_id[:60]:<60} -> doc_type={doc_type!r} "
        f"jurisdiction={doc_classification.get('jurisdiction')!r}"
    )

    if dry_run:
        return

    client.table("document_metadata").update({
        "doc_type": doc_type,
        "legal_entities": doc_intelligence,
    }).eq("file_id", file_id).execute()

    for row in chunk_rows:
        merged_metadata = dict(row.get("metadata") or {})
        merged_metadata.update({
            "doc_type": doc_type,
            "jurisdiction": doc_classification.get("jurisdiction", "Unknown"),
            "doc_language": doc_classification.get("language", "English"),
            "legal_entities": doc_intelligence,
        })
        client.table("documents").update({"metadata": merged_metadata}).eq("id", row["id"]).execute()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file-id", help="Re-classify only this file_id (default: every ingested file)")
    parser.add_argument("--dry-run", action="store_true", help="Classify and print results without writing")
    args = parser.parse_args()

    client = make_service_client()
    llm = make_chat_llm()

    file_ids = [args.file_id] if args.file_id else _list_file_ids(client)
    print(f"Re-classifying {len(file_ids)} file(s){' (dry run)' if args.dry_run else ''}...\n")

    for file_id in file_ids:
        try:
            _reclassify_one(client, llm, file_id, args.dry_run)
        except Exception as exc:  # noqa: BLE001
            print(f"  [error] {file_id}: {exc}", file=sys.stderr)

    print("\nDone." if not args.dry_run else "\nDry run complete — no writes made.")


if __name__ == "__main__":
    main()

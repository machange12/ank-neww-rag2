"""
dump_chunks_for_labeling.py
============================
Read-only helper for building scripts/golden_sets/retrieval_golden_set.json
by hand: prints every chunk for a given file_id with its documents.id, chunk
index, and section heading next to a text preview, so you can pick out the
right chunk ids without querying Supabase manually each time.

Usage:
    python scripts/dump_chunks_for_labeling.py <file_id>
    python scripts/dump_chunks_for_labeling.py <file_id> --preview-chars 300

Find file_ids via the app's document list endpoint, or:
    python scripts/dump_chunks_for_labeling.py --list-files
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from postgrest_utils import response_data
from search.service_client import make_service_client


def _list_files(client) -> None:
    resp = client.table("document_metadata").select("file_id, file_title, doc_type").execute()
    rows = response_data(resp) or []
    print(f"{'file_id':<50} {'doc_type':<12} title")
    print("-" * 100)
    for row in rows:
        print(f"{row.get('file_id', '')[:48]:<50} {row.get('doc_type', ''):<12} {row.get('file_title', '')}")


def _dump_chunks(client, file_id: str, preview_chars: int) -> None:
    resp = (
        client.table("documents")
        .select("id, content, metadata")
        .filter("metadata->>file_id", "eq", file_id)
        .execute()
    )
    rows = response_data(resp) or []
    if not rows:
        print(f"No chunks found for file_id={file_id!r}", file=sys.stderr)
        return

    def sort_key(row: dict) -> int:
        return (row.get("metadata") or {}).get("chunk_index", 0) or 0

    rows.sort(key=sort_key)

    for row in rows:
        meta = row.get("metadata") or {}
        preview = (row.get("content") or "").replace("\n", " ")[:preview_chars]
        print(f"id={row['id']}  chunk_index={meta.get('chunk_index')}  section={meta.get('section_heading') or '-'}")
        print(f"    {preview}...")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("file_id", nargs="?")
    parser.add_argument("--preview-chars", type=int, default=200)
    parser.add_argument("--list-files", action="store_true")
    args = parser.parse_args()

    client = make_service_client()

    if args.list_files or not args.file_id:
        _list_files(client)
        if not args.file_id:
            return
    _dump_chunks(client, args.file_id, args.preview_chars)


if __name__ == "__main__":
    main()

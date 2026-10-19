"""
reingest_provenance.py
======================
One-off backfill: give already-ingested documents a REAL page/paragraph
locator so citations can say "p. 12" / "¶ 4" instead of a guess.

Background
----------
Before the provenance change, ingest computed ``metadata.page_number`` as
``chunk_index // chunks_per_page`` — a synthetic number unrelated to the PDF
page the text came from, and 0 for every DOCX/text file. Along with the
chunking change (CHUNKING_VERSION v3), ``ingest/extract.py`` now keeps each
PDF page / DOCX paragraph as its own fragment and ``ingest/embed.py`` maps
every chunk back onto those spans, so the locator is derived from the source.

Two kinds of file, two different fixes
--------------------------------------
* **Drive-sourced documents** (``file_id`` not starting with ``upload_``):
  the bytes can be re-downloaded, so this script re-runs the full ingest with
  ``force=True`` (bypassing the unchanged-content short-circuit) and the new
  chunks get real provenance. ``access_level`` / ``matter_id`` /
  ``drive_modified_time`` are copied from ``document_metadata``, so the
  security classification is preserved exactly.

* **UI uploads** (``file_id`` starting with ``upload_``): the original bytes
  are NOT stored anywhere — ingest keeps only chunks — so pages cannot be
  recovered. Rather than keep showing a fabricated page, this script STRIPS
  the bogus locator from those chunks so the UI shows no page at all. The
  document becomes page-accurate the next time someone re-uploads it. (The
  file content itself is untouched; only metadata loses its fake page.)

Idempotent: re-running only touches files still missing a real locator
(re-ingested Drive files gain ``locator_kind``; stripped uploads no longer
carry ``page_number``).

Usage:
    python scripts/reingest_provenance.py                 # everything
    python scripts/reingest_provenance.py --dry-run       # report only
    python scripts/reingest_provenance.py --file-id <id>
    python scripts/reingest_provenance.py --only drive    # skip upload stripping
    python scripts/reingest_provenance.py --only uploads  # only strip uploads

Run this AFTER deploying the ingest change, from a machine with Drive
credentials (GOOGLE_* env / token.json) and the service-role key.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from postgrest_utils import response_data
from search.service_client import make_service_client

# Locator keys written by the pre-provenance ingest. The synthetic
# ``page_number`` is what actually misleads; the others never existed before
# v3 but are removed defensively.
_LEGACY_LOCATOR_KEYS = (
    "page_number",
    "page_start",
    "page_end",
    "paragraph_start",
    "paragraph_end",
    "locator_kind",
    "char_start",
    "char_end",
)


def _is_upload(file_id: str) -> bool:
    return file_id.startswith("upload_")


def _list_files(client: Any) -> list[dict[str, Any]]:
    rows = response_data(
        client.table("document_metadata")
        .select(
            "file_id, file_title, url, mime_type, access_level, matter_id, "
            "drive_modified_time, owner_user_id, doc_scope"
        )
        .execute()
    ) or []
    return list(rows)


def _file_has_provenance(client: Any, file_id: str) -> bool:
    """True when at least one chunk already carries a real locator."""
    rows = response_data(
        client.table("documents")
        .select("metadata")
        .filter("metadata->>file_id", "eq", file_id)
        .limit(50)
        .execute()
    ) or []
    for row in rows:
        meta = row.get("metadata") or {}
        if meta.get("locator_kind") in ("page", "paragraph"):
            return True
    return False


def _strip_fake_locators(client: Any, file_id: str, dry_run: bool) -> int:
    """Remove the synthetic page_number from an upload's chunks. Returns rows changed."""
    rows = response_data(
        client.table("documents")
        .select("id, metadata")
        .filter("metadata->>file_id", "eq", file_id)
        .limit(5000)
        .execute()
    ) or []
    changed = 0
    for row in rows:
        meta = dict(row.get("metadata") or {})
        if not any(key in meta for key in _LEGACY_LOCATOR_KEYS):
            continue
        for key in _LEGACY_LOCATOR_KEYS:
            meta.pop(key, None)
        changed += 1
        if not dry_run:
            client.table("documents").update({"metadata": meta}).eq("id", row["id"]).execute()
    return changed


async def _reingest_drive(client: Any, row: dict[str, Any], dry_run: bool) -> str:
    """Re-download + re-ingest one Drive file with force=True."""
    from ingest.downloader import download_file
    from ingest.store import upsert_file

    file_id = row["file_id"]
    mime_type = row.get("mime_type") or "application/pdf"
    if dry_run:
        return f"would re-download + re-ingest ({mime_type})"

    raw = download_file(file_id, mime_type)
    await upsert_file(
        file_id=file_id,
        file_title=row.get("file_title") or file_id,
        file_url=row.get("url") or "",
        mime_type=mime_type,
        raw_bytes=raw,
        access_level=int(row.get("access_level") or 1),
        matter_id=row.get("matter_id") or "",
        drive_modified_time=row.get("drive_modified_time") or "",
        force=True,
        # Preserve the privacy classification exactly: a re-ingest must never
        # publish a private upload (or private a corpus document).
        owner_user_id=row.get("owner_user_id"),
        doc_scope=row.get("doc_scope") or "corpus",
    )
    return f"re-ingested {len(raw):,} bytes"


async def _run(args: argparse.Namespace) -> int:
    client = make_service_client()
    rows = _list_files(client)
    if args.file_id:
        rows = [r for r in rows if r["file_id"] == args.file_id]
    if args.only == "drive":
        rows = [r for r in rows if not _is_upload(r["file_id"])]
    elif args.only == "uploads":
        rows = [r for r in rows if _is_upload(r["file_id"])]
    if args.limit:
        rows = rows[: args.limit]

    print(f"Checking {len(rows)} file(s){' (dry run)' if args.dry_run else ''}...\n")

    stats = {"drive": 0, "stripped": 0, "already": 0, "skipped": 0, "errors": 0}
    for row in rows:
        file_id = row["file_id"]
        label = (row.get("file_title") or file_id)[:60]
        try:
            if _file_has_provenance(client, file_id):
                stats["already"] += 1
                continue

            if _is_upload(file_id):
                if args.only == "drive":
                    stats["skipped"] += 1
                    continue
                changed = _strip_fake_locators(client, file_id, args.dry_run)
                if changed:
                    stats["stripped"] += 1
                    print(f"  [strip]  {label}: removed fake page from {changed} chunk(s) — re-upload for real pages")
                else:
                    stats["already"] += 1
                continue

            if args.only == "uploads":
                stats["skipped"] += 1
                continue

            detail = await _reingest_drive(client, row, args.dry_run)
            stats["drive"] += 1
            print(f"  [drive]  {label}: {detail}")
        except Exception as exc:  # noqa: BLE001
            stats["errors"] += 1
            print(f"  [error]  {file_id}: {exc}", file=sys.stderr)

    print(
        f"\nDone. drive re-ingested={stats['drive']} "
        f"uploads stripped={stats['stripped']} already ok={stats['already']} "
        f"skipped={stats['skipped']} errors={stats['errors']}"
    )
    if not args.dry_run and stats["stripped"]:
        print(
            "Reminder: UI uploads cannot recover pages (original bytes are not "
            "stored). Re-upload those files to get page-accurate citations."
        )
    return 1 if stats["errors"] else 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--file-id", help="Process only this file_id")
    parser.add_argument("--dry-run", action="store_true", help="Report what would change, write nothing")
    parser.add_argument("--limit", type=int, default=0, help="Stop after N files (0 = no limit)")
    parser.add_argument(
        "--only",
        choices=("drive", "uploads"),
        help="Process only Drive-sourced docs (re-ingest) or only uploads (strip)",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()

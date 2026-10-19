"""
import_drafting_library.py
==========================
Turn a folder of the firm's real templates / clauses / playbook positions into
the drafting library — without any of that content passing through a
developer's hands.

Two-step workflow
-----------------
1. Scaffold a manifest from a folder of documents::

       python scripts/import_drafting_library.py --scaffold drafting/library_sources

   That writes ``drafting/library_sources/library.json`` listing every file it
   found, with the metadata fields left for you to fill in (doc_type,
   clause_type, access_level, ...). Nothing is uploaded.

2. Edit the manifest, then apply it::

       python scripts/import_drafting_library.py drafting/library_sources --apply
       python scripts/import_drafting_library.py drafting/library_sources --apply --dry-run
       python scripts/import_drafting_library.py drafting/library_sources --apply --ingest

   ``--apply`` seeds the library (idempotent: rows with the same natural key are
   replaced). ``--ingest`` additionally indexes each source document into the
   firm corpus and records its file_id on the clause, so a clause citation can
   be traced back to the document it came from. ``--dry-run`` writes the
   assembled seed JSON to ``drafting/imported_seed_data.json`` and changes
   nothing.

Manifest format (``library.json``)
----------------------------------
::

    {
      "defaults": {"access_level": 2, "matter_id": "", "jurisdiction": "ke"},
      "templates": [
        {
          "title": "Share Purchase Agreement",
          "doc_type": "share_purchase",
          "practice_area": "Corporate",
          "description": "Buyer-side first draft.",
          "skeleton": [
            {"heading": "Definitions", "clause_type": "definitions", "guidance": "", "required": true}
          ]
        }
      ],
      "clauses": [
        {
          "title": "Force majeure",
          "clause_type": "force_majeure",
          "doc_type": "share_purchase",
          "file": "clauses/force-majeure.docx"
        },
        {"title": "Governing law", "file": "clauses/governing-law.docx"},
        {"title": "Entire agreement", "body": "inline clause text ..."}
      ],
      "playbooks": [
        {
          "name": "SPA — seller side",
          "doc_type": "share_purchase",
          "rules": [
            {"clause_type": "indemnity", "position": "must_reject", "rule_text": "...", "severity": "high"}
          ]
        }
      ]
    }

* ``file`` paths are relative to the manifest's folder. DOCX, PDF, TXT and MD
  are all accepted (the same extractor ingest uses).
* ``body`` takes precedence over ``file`` when both are present.
* ``defaults`` values are applied to every entry that does not set them itself.

This is a service-role CURATION write (like scripts/seed_drafting_library.py)
and is never reachable from a user-facing request.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"
MANIFEST_NAME = "library.json"
DRY_RUN_OUT = _PROJECT_ROOT / "drafting" / "imported_seed_data.json"

SUPPORTED_SUFFIXES = {".docx", ".pdf", ".txt", ".md", ".markdown"}

_MIME_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/plain",
    ".markdown": "text/plain",
}

# Per-file presence is informational only — a real clause can be one paragraph
# long. It exists so a scaffolder can eyeball obviously-empty extractions
# before applying them.
_MIN_BODY_CHARS = 40


def _mime_for(path: Path) -> str:
    return _MIME_TYPES.get(path.suffix.lower(), "application/octet-stream")


def _read_text(path: Path) -> str:
    """Extract plain text from one library source document.

    ``extract_text`` returns ``(text, page_count)``; only the text matters here.
    """
    from ingest.extract import extract_text

    text, _pages = extract_text(path.read_bytes(), _mime_for(path))
    return text.strip()


def _body_for(entry: dict[str, Any], base_dir: Path) -> str:
    """Clause body: inline ``body`` wins, else the ``file`` it points at."""
    if (entry.get("body") or "").strip():
        return entry["body"].strip()
    rel = (entry.get("file") or "").strip()
    if not rel:
        raise ValueError(f"clause {entry.get('title')!r}: needs 'body' or 'file'")
    path = (base_dir / rel).resolve()
    if not path.exists():
        raise ValueError(f"clause {entry.get('title')!r}: file not found: {rel}")
    return _read_text(path)


def _with_defaults(entry: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    merged = dict(entry)
    for key, value in defaults.items():
        merged.setdefault(key, value)
    return merged


def build_seed_data(manifest: dict[str, Any], base_dir: Path) -> dict[str, Any]:
    """Assemble a ``seed_library`` document from a manifest + its source files."""
    defaults = manifest.get("defaults") or {}

    templates = [_with_defaults(tpl, defaults) for tpl in manifest.get("templates") or []]
    playbooks = [_with_defaults(pb, defaults) for pb in manifest.get("playbooks") or []]

    clauses: list[dict[str, Any]] = []
    for raw in manifest.get("clauses") or []:
        entry = _with_defaults(raw, defaults)
        body = _body_for(entry, base_dir)
        entry["body"] = body
        entry.pop("file", None)
        clauses.append(entry)

    return {"templates": templates, "clauses": clauses, "playbooks": playbooks}


def _scaffold(folder: Path) -> Path:
    """List every supported document in ``folder`` as an editable manifest."""
    found = sorted(
        p for p in folder.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES and p.name != MANIFEST_NAME
    )
    clauses = []
    for path in found:
        rel = path.relative_to(folder).as_posix()
        clauses.append({
            "title": path.stem.replace("_", " ").replace("-", " ").strip(),
            "clause_type": path.stem.lower().replace(" ", "_").replace("-", "_"),
            "doc_type": "",
            "file": rel,
        })

    manifest = {
        "_comment": (
            "Fill in doc_type / practice_area / access_level, move entries to "
            "'templates' or 'playbooks' where appropriate, then run this script "
            "with --apply. 'access_level' is the classification ceiling for the "
            "clause (1 = usable firm-wide at every level)."
        ),
        "defaults": {"access_level": 1, "matter_id": "", "jurisdiction": "ke"},
        "templates": [],
        "clauses": clauses,
        "playbooks": [],
    }

    out = folder / MANIFEST_NAME
    if out.exists():
        raise SystemExit(f"refusing to overwrite existing {out}")
    out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


def _ingest_and_annotate(client: Any, seed: dict[str, Any], manifest: dict[str, Any], base_dir: Path,
                         tenant_id: str, access_level_default: int) -> int:
    """Index every source document into the firm corpus and record its file_id.

    Clause rows keep a ``source_file_id`` so a clause can be traced to the
    document it was taken from, and so retrieval can use the same corpus.
    """
    import asyncio

    from ingest.store import upsert_file

    by_title = {c["title"]: c for c in seed["clauses"]}
    ingested = 0
    for raw in manifest.get("clauses") or []:
        rel = (raw.get("file") or "").strip()
        if not rel:
            continue
        path = (base_dir / rel).resolve()
        if not path.exists():
            continue
        entry = by_title.get(raw.get("title") or "")
        if entry is None:
            continue
        file_id = f"library_{tenant_id}_{path.stem}_{path.suffix.lstrip('.')}"
        asyncio.run(upsert_file(
            file_id=file_id,
            file_title=path.name,
            file_url="",
            mime_type=_mime_for(path),
            raw_bytes=path.read_bytes(),
            access_level=int(entry.get("access_level", access_level_default)),
            matter_id=entry.get("matter_id", "") or "",
            # Library documents are firm corpus, not one account's private
            # upload: accessible per access level + matter, never owner-only.
            doc_scope="corpus",
        ))
        entry["source_file_id"] = file_id
        ingested += 1
    return ingested


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "folder",
        nargs="?",
        default=str(_PROJECT_ROOT / "drafting" / "library_sources"),
        help="folder holding the manifest and its source documents",
    )
    parser.add_argument("--scaffold", action="store_true", help="write a starter library.json from the folder")
    parser.add_argument("--apply", action="store_true", help="seed the library from the manifest")
    parser.add_argument("--dry-run", action="store_true", help="write the assembled seed JSON, seed nothing")
    parser.add_argument("--ingest", action="store_true", help="also index each source document into the corpus")
    parser.add_argument("--tenant-id", default=DEFAULT_TENANT_ID, help="tenant id to attach rows to")
    parser.add_argument("--manifest", default=MANIFEST_NAME, help="manifest filename inside the folder")
    args = parser.parse_args()

    folder = Path(args.folder).resolve()
    if args.scaffold:
        if not folder.exists():
            print(f"folder not found: {folder}", file=sys.stderr)
            return 2
        out = _scaffold(folder)
        print(f"Wrote {out}\nEdit it, then re-run with --apply.")
        return 0

    if not args.apply:
        parser.error("choose --scaffold or --apply")

    manifest_path = folder / args.manifest
    if not manifest_path.exists():
        print(
            f"manifest not found: {manifest_path}\n"
            f"Run with --scaffold to generate one.",
            file=sys.stderr,
        )
        return 2

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    try:
        seed = build_seed_data(manifest, folder)
    except ValueError as exc:
        print(f"manifest error: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        DRY_RUN_OUT.write_text(json.dumps(seed, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(
            f"Dry run: {len(seed['templates'])} templates, {len(seed['clauses'])} clauses, "
            f"{len(seed['playbooks'])} playbooks.\nWrote {DRY_RUN_OUT} — nothing was seeded."
        )
        return 0

    from drafting.library import seed_library
    from search.service_client import make_service_client

    client = make_service_client()

    if args.ingest:
        default_level = int((manifest.get("defaults") or {}).get("access_level", 1))
        count = _ingest_and_annotate(client, seed, manifest, folder, args.tenant_id, default_level)
        print(f"Indexed {count} source document(s) into the firm corpus.")

    summary = seed_library(client, seed, tenant_id=args.tenant_id)
    print(
        "Seeded drafting library: "
        f"{summary['templates']} templates, {summary['clauses']} clauses, "
        f"{summary['playbooks']} playbooks."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

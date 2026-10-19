"""seed_drafting_library.py
=========================
Load the curated drafting library (templates, clause bank, playbooks) into
Supabase. This is a service-role CURATION write, run by an operator — it is
not reachable from any user-facing request.

Usage:
    python scripts/seed_drafting_library.py [data.json] [--tenant-id UUID]

Defaults to lawfirm-rag/drafting/seed_data.json and the single-firm tenant.
Re-running is safe: rows with the same natural key are replaced, not duplicated.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

DEFAULT_DATA = _PROJECT_ROOT / "drafting" / "seed_data.json"
DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the drafting library.")
    parser.add_argument("data", nargs="?", default=str(DEFAULT_DATA), help="seed JSON file")
    parser.add_argument("--tenant-id", default=DEFAULT_TENANT_ID, help="tenant id to attach rows to")
    args = parser.parse_args()

    data_path = Path(args.data)
    if not data_path.exists():
        print(f"seed file not found: {data_path}", file=sys.stderr)
        return 2

    data = json.loads(data_path.read_text(encoding="utf-8"))

    from drafting.library import seed_library
    from search.service_client import make_service_client

    client = make_service_client()
    summary = seed_library(client, data, tenant_id=args.tenant_id)
    print(
        "Seeded drafting library: "
        f"{summary['templates']} templates, {summary['clauses']} clauses, "
        f"{summary['playbooks']} playbooks."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
reembed_all.py
==============
Re-embed every chunk in `documents` with the currently configured embedder
(after changing REMOTE_EMBED_MODEL / provider). Rebuilds the exact embedding
input ingestion uses (ingest.embed.enrich_chunk) from each stored row.

Backs up the old vectors to a JSON file first, so a switch can be reverted:

    python scripts/reembed_all.py backup.json            # re-embed
    python scripts/reembed_all.py backup.json --restore  # put old vectors back
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import openai  # noqa: E402

from ingest.embed import TextChunk, enrich_chunk  # noqa: E402
from providers import get_embeddings  # noqa: E402
from search.supabase_client import make_service_client  # noqa: E402

BATCH = 10


def fetch_all(client) -> list[dict]:
    rows, start = [], 0
    while True:
        page = client.table("documents").select("id,content,metadata,embedding").range(start, start + 499).execute().data
        rows += page
        if len(page) < 500:
            return rows
        start += 500


def main() -> None:
    backup = Path(sys.argv[1])
    client = make_service_client()

    if "--restore" in sys.argv:
        for row in json.loads(backup.read_text()):
            client.table("documents").update({"embedding": row["embedding"]}).eq("id", row["id"]).execute()
        print("restored", backup)
        return

    rows = fetch_all(client)
    if backup.exists():
        # Keep the earliest backup: it holds the vectors from before any run.
        print(f"keeping existing backup {backup}")
    else:
        backup.write_text(json.dumps([{"id": r["id"], "embedding": r["embedding"]} for r in rows]))
        print(f"backed up {len(rows)} vectors to {backup}")

    texts = [
        enrich_chunk(
            TextChunk(r["content"], (r["metadata"] or {}).get("section_heading") or "", 0),
            (r["metadata"] or {}).get("file_title") or "",
        )
        for r in rows
    ]
    # Small batches + back-off: free tiers (Gemini) cap tokens per minute
    # well below one 100-chunk batch.
    embedder = get_embeddings()
    for start in range(0, len(rows), BATCH):
        batch = texts[start:start + BATCH]
        for attempt in range(8):
            try:
                vectors = embedder.embed_documents(batch)
                break
            except openai.RateLimitError:
                print("  rate limited, waiting 20s")
                time.sleep(20)
        else:
            sys.exit(f"gave up at chunk {start}; rerun to continue (backup kept)")
        for row, vec in zip(rows[start:start + BATCH], vectors):
            client.table("documents").update({"embedding": vec}).eq("id", row["id"]).execute()
        print(f"  updated {min(start + BATCH, len(rows))}/{len(rows)}")
    print(f"re-embedded {len(rows)} chunks")


if __name__ == "__main__":
    main()

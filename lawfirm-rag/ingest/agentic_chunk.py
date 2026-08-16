"""Optional LLM-boundary chunking pre-pass.

``ingest/embed.py``'s legal-aware ``RecursiveCharacterTextSplitter`` is the
default and the fallback on ANY failure here — this module never blocks or
fails ingest. Gated by ``settings.use_agentic_chunking`` (default False).

Do not flip this on in production without first A/B-testing it against the
recursive splitter on your real corpus via a golden query set (see
``scripts/eval_retrieval.py``) — 2025-26 benchmarks (Vectara/NAACL) show
agentic chunking often doesn't beat a well-tuned recursive splitter except on
structured, high-stakes text, and this is genuinely worth testing rather than
assuming for this corpus, but that test needs real documents and real
queries this module can't manufacture.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from ingest.embed import TextChunk

logger = logging.getLogger(__name__)

# Conservative cap so one oversized document can't produce a huge/expensive
# LLM call or a response too large to parse reliably. Documents over this are
# skipped straight to the recursive splitter (also logged) rather than
# truncated — truncating a legal document before chunking it risks silently
# dropping clauses from the index.
AGENTIC_CHUNK_MAX_CHARS = 24_000

AGENTIC_CHUNK_PROMPT = """You are splitting a legal document into retrieval chunks for a RAG system.

Split the document below into coherent chunks along natural legal boundaries \
(clauses, sections, schedules) — never mid-sentence or mid-clause. Each chunk \
should be self-contained enough to be understood with its section heading. \
Prefer chunks of roughly 400-900 words; do not create very short (<50 word) \
chunks unless the source clause itself is that short.

Reply with JSON only, no other text: a list of objects, each
{{"text": "<verbatim chunk text, copied exactly from the document>", \
"section_heading": "<best short heading for this chunk, or empty string>"}}.

DOCUMENT:
{document}
"""


def agentic_chunk_text(text: str, llm: Any) -> list[TextChunk] | None:
    """
    Ask an LLM to propose chunk boundaries for a legal document.

    Returns None on any failure (oversized input, LLM error, unparseable or
    empty response, a chunk whose text doesn't actually appear in the
    source) so the caller falls back to the recursive splitter. Never
    raises — a failure here must never break ingest.
    """
    if len(text) > AGENTIC_CHUNK_MAX_CHARS:
        logger.info(
            "agentic chunking skipped (document is %d chars, over the %d cap) — using recursive splitter",
            len(text),
            AGENTIC_CHUNK_MAX_CHARS,
        )
        return None

    try:
        response = llm.invoke(AGENTIC_CHUNK_PROMPT.format(document=text))
        content = getattr(response, "content", "") or ""
        if not isinstance(content, str):
            content = str(content)

        match = re.search(r"\[.*\]", content, re.DOTALL)
        parsed = json.loads(match.group(0)) if match else json.loads(content)

        if not isinstance(parsed, list) or not parsed:
            raise ValueError("LLM response was not a non-empty JSON array")

        chunks: list[TextChunk] = []
        for i, item in enumerate(parsed):
            chunk_text_str = (item.get("text") or "").strip()
            # Verbatim-ness check: reject chunks the LLM paraphrased or
            # hallucinated rather than copied — citations must be able to
            # point at real source text.
            if not chunk_text_str or chunk_text_str not in text:
                raise ValueError(f"chunk {i} is empty or not verbatim from the source document")
            chunks.append(TextChunk(
                text=chunk_text_str,
                section_heading=(item.get("section_heading") or "").strip(),
                chunk_index=i,
            ))
        return chunks
    except Exception as exc:  # noqa: BLE001
        logger.warning("agentic chunking failed, falling back to recursive splitter: %s", exc)
        return None

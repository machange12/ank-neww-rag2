from __future__ import annotations

import re
from typing import Any, NamedTuple

import tiktoken
from langchain_text_splitters import RecursiveCharacterTextSplitter

from config import settings
from providers import get_embeddings

# Bumped whenever the chunking strategy changes (splitter, length function,
# size/overlap, separator set) in a way that changes chunk boundaries for
# existing documents. Stored on every chunk's metadata (ingest/store.py) so
# chunks from different chunking runs are distinguishable and a re-ingest can
# be scoped/audited instead of guessed at. v1 was character-based
# (RecursiveCharacterTextSplitter default len()); v2 switched to token-based
# (tiktoken cl100k_base) with larger chunks and less overlap — see
# config.py's chunk_size/chunk_overlap docstring for the reasoning.
CHUNKING_VERSION = "legal-recursive-tiktoken-v2"

_TOKENIZER = tiktoken.get_encoding("cl100k_base")


def _token_length(text: str) -> int:
    """Token count via tiktoken cl100k_base — a close proxy for the actual
    embedding model's tokenizer and a much more meaningful unit than raw
    character count against an 8192-token-context embedder."""
    return len(_TOKENIZER.encode(text, disallowed_special=()))


# Legal document separators ordered from largest structural unit to smallest.
# Kenyan statutes and contracts follow predictable heading patterns —
# splitting on these first keeps clauses and sub-clauses intact.
LEGAL_SEPARATORS = [
    # Part and schedule headings
    r"\n(?=PART\s+[IVXLCDM]+\b)",
    r"\n(?=SCHEDULE\s+[IVXLCDM\d]+\b)",
    r"\n(?=ANNEX(?:URE)?\s+[IVXLCDM\d]+\b)",
    r"\n(?=APPENDIX\s+[IVXLCDM\d]+\b)",
    # Contract boilerplate markers
    r"\n(?=WHEREAS\b)",
    r"\n(?=NOW[\s,]+THEREFORE\b)",
    r"\n(?=PROVIDED\s+THAT\b)",
    r"\n(?=IN\s+WITNESS\s+WHEREOF\b)",
    r"\n(?=IT\s+IS\s+HEREBY\s+AGREED\b)",
    # Kenyan statute section headings e.g. "Section 4." or "Section 4A."
    r"\n(?=Section\s+\d+[A-Z]?\.)",
    # Numbered clause e.g. "14." or "14A." at start of line
    r"\n(?=\d+[A-Z]?\.\s+[A-Z])",
    # Sub-clause e.g. "14.1" or "14.1.2"
    r"\n(?=\d+\.\d+)",
    # Lettered sub-clause e.g. "(a)" or "(i)"
    r"\n(?=\([a-z]\)|\([ivxlcdm]+\))",
    # Standard paragraph breaks
    "\n\n",
    "\n",
    ". ",
    " ",
    "",
]


class TextChunk(NamedTuple):
    text: str
    section_heading: str
    chunk_index: int


def _detect_heading(text: str) -> str:
    """
    Extract the most likely section/clause heading from the start of a chunk.
    Returns empty string if no heading detected.
    """
    first_line = text.strip().split("\n")[0].strip()

    patterns = [
        # "PART IV — MISCELLANEOUS"
        r"^(PART\s+[IVXLCDM]+\b.*)",
        # "SCHEDULE 1" or "SCHEDULE A"
        r"^(SCHEDULE\s+[\dIVXLCDMA-Z]+\b.*)",
        # "Section 14." or "Section 14A."
        r"^(Section\s+\d+[A-Z]?\..*)",
        # "14." or "14A." followed by capital — numbered clause
        r"^(\d+[A-Z]?\.\s+[A-Z][^.]{0,60})",
        # "14.1" sub-clause
        r"^(\d+\.\d+[\.\d]*\s+.*)",
        # "WHEREAS", "NOW THEREFORE" etc.
        r"^(WHEREAS\b.*|NOW[\s,]+THEREFORE\b.*|PROVIDED\s+THAT\b.*)",
    ]

    for pattern in patterns:
        match = re.match(pattern, first_line, re.IGNORECASE)
        if match:
            heading = match.group(1).strip()
            # Truncate very long headings
            return heading[:120] if len(heading) > 120 else heading

    return ""


def chunk_text(text: str) -> list[TextChunk]:
    """
    Split legal document text into chunks using Kenyan legal document structure.
    Returns list of TextChunk namedtuples with text, section_heading, and chunk_index.
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        separators=LEGAL_SEPARATORS,
        length_function=_token_length,
        is_separator_regex=True,
    )

    raw_chunks = splitter.split_text(text)

    result: list[TextChunk] = []
    last_heading = ""

    for i, chunk_text_str in enumerate(raw_chunks):
        heading = _detect_heading(chunk_text_str)
        # Carry forward the last known heading if this chunk has none
        # (e.g. continuation of a long clause)
        if heading:
            last_heading = heading
        result.append(TextChunk(
            text=chunk_text_str,
            section_heading=last_heading,
            chunk_index=i,
        ))

    return result


def total_tokens(chunks: list[TextChunk]) -> int:
    """Total tiktoken count across all chunks — for ingest-time logging so
    chunk-count/token-count trends are visible per file, not just guessed at."""
    return sum(_token_length(chunk.text) for chunk in chunks)


def chunk_text_auto(text: str, llm: Any = None) -> tuple[list[TextChunk], str]:
    """
    Chunk ``text``, optionally trying the LLM-boundary pre-pass first.

    When ``settings.use_agentic_chunking`` is on and ``llm`` is provided,
    tries ``ingest.agentic_chunk.agentic_chunk_text`` first and falls back to
    the recursive splitter on any failure (oversized doc, LLM error,
    unparseable/non-verbatim response) — see agentic_chunk.py. Returns
    ``(chunks, method)`` where ``method`` is ``"agentic"`` or ``"recursive"``
    so callers can record which one actually produced these chunks.
    """
    if settings.use_agentic_chunking and llm is not None:
        # Local import: agentic_chunk.py imports TextChunk from this module,
        # so a module-level import here would be circular.
        from ingest.agentic_chunk import agentic_chunk_text

        agentic_result = agentic_chunk_text(text, llm)
        if agentic_result is not None:
            return agentic_result, "agentic"

    return chunk_text(text), "recursive"


def enrich_chunk(chunk: TextChunk, file_title: str, total_pages: int = 0) -> str:
    """
    Prefix a chunk with document and section context for embedding only.

    This is the "contextual retrieval" pattern: chunks that rely on surrounding
    context ("as defined above", "the preceding clause") embed far better when
    the document title and section heading are visible to the model. The stored
    ``chunk.text`` is intentionally NOT modified — citations keep showing clean
    text, and only the embedding input carries the enrichment.

    ``total_pages`` is reserved for future page-aware enrichment.
    """
    section = chunk.section_heading or "General"
    return f"Document: {file_title}\nSection: {section}\n\n{chunk.text}"


def embed_chunks(chunks: list[TextChunk], file_title: str = "") -> list[list[float]]:
    """
    Embed a list of chunks (Groq/HuggingFace nomic, or OpenAI fallback).

    Each chunk is enriched with its document title and section heading before
    embedding. Only the embedding input changes — the original chunk text is
    never rewritten.
    """
    enriched_texts = [enrich_chunk(chunk, file_title) for chunk in chunks]
    embedder = get_embeddings()
    return embedder.embed_documents(enriched_texts)
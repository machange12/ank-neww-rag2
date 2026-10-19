from __future__ import annotations

import re
from typing import Any, NamedTuple

import tiktoken
from langchain_text_splitters import RecursiveCharacterTextSplitter

from config import settings
from ingest.extract import Segment
from providers import embed_prefixes, get_bulk_embeddings

# Bumped whenever the chunking strategy changes (splitter, length function,
# size/overlap, separator set) in a way that changes chunk boundaries for
# existing documents. Stored on every chunk's metadata (ingest/store.py) so
# chunks from different chunking runs are distinguishable and a re-ingest can
# be scoped/audited instead of guessed at. v1 was character-based
# (RecursiveCharacterTextSplitter default len()); v2 switched to token-based
# (tiktoken cl100k_base) with larger chunks and less overlap — see
# config.py's chunk_size/chunk_overlap docstring for the reasoning. v3 adds
# real page/paragraph provenance (page_start/page_end, paragraph_start/
# paragraph_end, char offsets); boundaries for a given input are unchanged,
# but the stamped metadata is not, so a forced re-ingest is required to
# populate it for existing documents.
CHUNKING_VERSION = "legal-recursive-tiktoken-v3"

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
    # Provenance. Populated by chunk_text()/chunk_text_auto() from the
    # page/paragraph segments produced by ingest.extract. Defaults keep
    # positional constructors (scripts/reembed_all.py) and the agentic chunker
    # working unchanged.
    page_start: int | None = None
    page_end: int | None = None
    paragraph_start: int | None = None
    paragraph_end: int | None = None
    char_start: int = 0
    char_end: int = 0
    locator_kind: str = "none"  # "page" | "paragraph" | "none"


def _locate(
    start: int, end: int, segments: tuple[Segment, ...]
) -> tuple[int | None, int | None, int | None, int | None]:
    """Map a character span onto the page/paragraph segments it overlaps.

    A chunk that straddles a page break reports a range (start, end); a chunk
    inside one page reports the same number for both. Returns Nones when the
    document has no such locator (e.g. plain text without paragraphs).
    """
    overlapping = [s for s in segments if s.start < end and s.end > start]
    pages = [s.page for s in overlapping if s.page is not None]
    paragraphs = [s.paragraph for s in overlapping if s.paragraph is not None]
    return (
        min(pages) if pages else None,
        max(pages) if pages else None,
        min(paragraphs) if paragraphs else None,
        max(paragraphs) if paragraphs else None,
    )


def _apply_provenance(
    chunks: list[TextChunk],
    segments: tuple[Segment, ...] | None,
    locator_kind: str,
) -> list[TextChunk]:
    if not segments:
        return chunks
    out: list[TextChunk] = []
    for chunk in chunks:
        if chunk.char_start < 0:
            # Offset unknown (a chunk the agentic splitter could not be located
            # in the source). Leave the locator empty rather than attaching the
            # first page — an absent page is honest, a wrong one is not.
            out.append(chunk)
            continue
        page_start, page_end, para_start, para_end = _locate(
            chunk.char_start, chunk.char_end, segments
        )
        out.append(
            chunk._replace(
                page_start=page_start,
                page_end=page_end,
                paragraph_start=para_start,
                paragraph_end=para_end,
                locator_kind=locator_kind,
            )
        )
    return out


def _attach_offsets(chunks: list[TextChunk], text: str) -> list[TextChunk]:
    """Recover char offsets for chunks produced without them (agentic path).

    Those chunks are verified verbatim and ordered by the chunker, so a
    forward-only cursor is unambiguous and cheap. A chunk that cannot be found
    at all keeps ``char_start = -1`` so it is reported with no locator instead
    of being pinned to the start of the document.
    """
    out: list[TextChunk] = []
    cursor = 0
    for chunk in chunks:
        index = text.find(chunk.text, cursor)
        if index < 0:
            index = text.find(chunk.text)
        if index < 0:
            out.append(chunk._replace(char_start=-1, char_end=-1))
            continue
        out.append(chunk._replace(char_start=index, char_end=index + len(chunk.text)))
        cursor = max(cursor, index + 1)
    return out


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


def chunk_text(
    text: str,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
    segments: tuple[Segment, ...] | None = None,
    locator_kind: str = "none",
) -> list[TextChunk]:
    """
    Split legal document text into chunks using Kenyan legal document structure.
    Returns list of TextChunk namedtuples with text, section_heading, chunk_index
    and (when ``segments`` is supplied) page/paragraph provenance.

    ``chunk_size``/``chunk_overlap`` default to settings.chunk_size/chunk_overlap
    (the production values) when omitted. The override params exist so
    scripts/experiment_chunk_sizes.py can compare sizes against a golden set
    without touching production ingest defaults.

    ``segments`` is the page/paragraph map from ``ingest.extract``. When given,
    chunks are produced via ``create_documents(add_start_index=True)`` so each
    chunk's character span is exact; the emitted chunk boundaries are identical
    to ``split_text``'s.
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size if chunk_size is not None else settings.chunk_size,
        chunk_overlap=chunk_overlap if chunk_overlap is not None else settings.chunk_overlap,
        separators=LEGAL_SEPARATORS,
        length_function=_token_length,
        is_separator_regex=True,
        add_start_index=bool(segments),
    )

    if segments:
        pieces = [
            (document.page_content, int(document.metadata.get("start_index", 0)))
            for document in splitter.create_documents([text])
        ]
    else:
        pieces = [(raw, 0) for raw in splitter.split_text(text)]

    result: list[TextChunk] = []
    last_heading = ""

    for i, (chunk_text_str, start) in enumerate(pieces):
        heading = _detect_heading(chunk_text_str)
        # Carry forward the last known heading if this chunk has none
        # (e.g. continuation of a long clause)
        if heading:
            last_heading = heading
        result.append(TextChunk(
            text=chunk_text_str,
            section_heading=last_heading,
            chunk_index=i,
            char_start=start,
            char_end=start + len(chunk_text_str),
        ))

    return _apply_provenance(result, segments, locator_kind)


def total_tokens(chunks: list[TextChunk]) -> int:
    """Total tiktoken count across all chunks — for ingest-time logging so
    chunk-count/token-count trends are visible per file, not just guessed at."""
    return sum(_token_length(chunk.text) for chunk in chunks)


def chunk_text_auto(
    text: str,
    llm: Any = None,
    segments: tuple[Segment, ...] | None = None,
    locator_kind: str = "none",
) -> tuple[list[TextChunk], str]:
    """
    Chunk ``text``, optionally trying the LLM-boundary pre-pass first.

    When ``settings.use_agentic_chunking`` is on and ``llm`` is provided,
    tries ``ingest.agentic_chunk.agentic_chunk_text`` first and falls back to
    the recursive splitter on any failure (oversized doc, LLM error,
    unparseable/non-verbatim response) — see agentic_chunk.py. Returns
    ``(chunks, method)`` where ``method`` is ``"agentic"`` or ``"recursive"``
    so callers can record which one actually produced these chunks.

    ``segments``/``locator_kind`` come from ``ingest.extract.extract_document``;
    both chunking paths are mapped onto them so citations carry a real page or
    paragraph.
    """
    if settings.use_agentic_chunking and llm is not None:
        # Local import: agentic_chunk.py imports TextChunk from this module,
        # so a module-level import here would be circular.
        from ingest.agentic_chunk import agentic_chunk_text

        agentic_result = agentic_chunk_text(text, llm)
        if agentic_result is not None:
            located = _attach_offsets(agentic_result, text)
            return _apply_provenance(located, segments, locator_kind), "agentic"

    return chunk_text(text, segments=segments, locator_kind=locator_kind), "recursive"


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
    _, doc_prefix = embed_prefixes()
    return f"{doc_prefix}Document: {file_title}\nSection: {section}\n\n{chunk.text}"


def embed_chunks(chunks: list[TextChunk], file_title: str = "") -> list[list[float]]:
    """
    Embed a list of chunks (Groq/HuggingFace nomic, or OpenAI fallback).

    Each chunk is enriched with its document title and section heading before
    embedding. Only the embedding input changes — the original chunk text is
    never rewritten.
    """
    enriched_texts = [enrich_chunk(chunk, file_title) for chunk in chunks]
    # Ingestion uses the bulk embedder — routes to a remote GPU endpoint
    # (Kaggle notebook) when REMOTE_EMBED_URL is set, else the local model.
    embedder = get_bulk_embeddings()
    return embedder.embed_documents(enriched_texts)
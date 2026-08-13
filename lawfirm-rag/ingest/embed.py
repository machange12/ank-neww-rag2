from __future__ import annotations

import re
from typing import NamedTuple

<<<<<<< HEAD
from langchain_text_splitters import RecursiveCharacterTextSplitter

from config import settings
from providers import get_embeddings
=======
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings

from config import settings
>>>>>>> c7d4b0571d87621b092e195d03135e276042d2fc


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
        length_function=len,
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


<<<<<<< HEAD
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
=======
def embed_chunks(chunks: list[str]) -> list[list[float]]:
    """Embed a list of text strings using OpenAI embeddings."""
    embedder = OpenAIEmbeddings(
        model=settings.embedding_model,
        openai_api_key=settings.openai_api_key,
    )
    return embedder.embed_documents(chunks)
>>>>>>> c7d4b0571d87621b092e195d03135e276042d2fc

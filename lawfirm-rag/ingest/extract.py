from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Provenance model
# ---------------------------------------------------------------------------
#
# Citations need to point at the *place* in the source, not just the document.
# The loaders below therefore keep every page (PDF) / paragraph (DOCX, text)
# as a separate fragment and record the character span of each fragment in the
# cleaned text. ``chunk_text`` then maps every chunk back onto those spans.
#
# PDF has intrinsic pages, so ``locator_kind = "page"``.
# DOCX/TXT/MD have no intrinsic pages (Word paginates at render time, based on
# fonts/margins/printer), so the honest locator is the paragraph number:
# ``locator_kind = "paragraph"``. Paragraph numbers are 1-based over the
# *non-empty* paragraphs in document order, which is also what a reader sees
# when they count paragraphs in Word.


@dataclass(frozen=True)
class Segment:
    """A contiguous span of the cleaned text, with its source locator."""

    start: int
    end: int
    page: int | None = None
    paragraph: int | None = None


@dataclass(frozen=True)
class ExtractedDocument:
    text: str
    total_pages: int
    segments: tuple[Segment, ...]
    locator_kind: str  # "page" | "paragraph" | "none"


# Lines repeated at least this often across the document are running headers /
# footers (e.g. "CONFIDENTIAL" on every page) and are removed. This is the
# pre-existing heuristic from clean_text, now applied per fragment so that
# offsets survive.
_REPEAT_THRESHOLD = 3


def _repeated_lines(fragments: list[str]) -> set[str]:
    counts: dict[str, int] = {}
    for fragment in fragments:
        for line in fragment.splitlines():
            stripped = line.strip()
            if stripped:
                counts[stripped] = counts.get(stripped, 0) + 1
    return {line for line, count in counts.items() if count >= _REPEAT_THRESHOLD}


def _clean_fragment(text: str, drop_lines: set[str]) -> str:
    """Apply the cleaning rules to one page/paragraph.

    Identical to the original ``clean_text`` except that ``drop_lines`` is
    supplied by the caller, so running headers are detected across the whole
    document while cleaning stays within a single fragment (keeping offsets
    meaningful).
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    kept = [
        line
        for line in lines
        if not line.strip() or line.strip() not in drop_lines
    ]
    cleaned = "\n".join(kept)

    cleaned = re.sub(r"(\w)-\n(\w)", r"\1 \2", cleaned)
    cleaned = re.sub(r"(?im)^\s*(?:\d+|page\s+\d+\s+of\s+\d+|-\s*\d+\s*-)\s*$", "", cleaned)
    cleaned = re.sub(r"[ \t\f\v]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def clean_text(text: str) -> str:
    """Clean extraction artifacts common in PDFs and legal documents.

    Kept as a public helper (tests and one-off scripts use it); it treats the
    whole string as a single fragment.
    """
    return _clean_fragment(text, _repeated_lines([text]))


def _assemble(
    fragments: list[tuple[str, int | None, int | None]],
) -> tuple[str, tuple[Segment, ...]]:
    """Join cleaned fragments with a blank line and record each span's offsets."""
    out = ""
    segments: list[Segment] = []
    for fragment, page, paragraph in fragments:
        if not fragment:
            continue
        if out:
            out += "\n\n"
        start = len(out)
        out += fragment
        segments.append(
            Segment(start=start, end=len(out), page=page, paragraph=paragraph)
        )
    return out, tuple(segments)


# ---------------------------------------------------------------------------
# Public extraction API
# ---------------------------------------------------------------------------


def extract_document(raw_bytes: bytes, mime_type: str) -> ExtractedDocument:
    """Extract cleaned text plus page/paragraph provenance."""
    if mime_type == "application/pdf":
        pages = _pdf_pages(raw_bytes)
        drop = _repeated_lines(pages)
        cleaned = [_clean_fragment(page, drop) for page in pages]
        text, segments = _assemble(
            [(fragment, index + 1, None) for index, fragment in enumerate(cleaned)]
        )
        return ExtractedDocument(
            text=text,
            total_pages=len(pages),
            segments=segments,
            locator_kind="page",
        )

    if mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        paragraphs = _docx_paragraphs(raw_bytes)
        drop = _repeated_lines(paragraphs)
        cleaned = [_clean_fragment(p, drop) for p in paragraphs]
        text, segments = _assemble(
            [(fragment, None, index + 1) for index, fragment in enumerate(cleaned)]
        )
        return ExtractedDocument(
            text=text,
            total_pages=1,
            segments=segments,
            locator_kind="paragraph",
        )

    # Google Docs are exported by Drive as text/plain; any other text type
    # lands here too. Paragraphs are blank-line separated.
    raw = raw_bytes.decode("utf-8", errors="replace")
    paragraphs = [p for p in re.split(r"\n\s*\n", raw) if p.strip()]
    drop = _repeated_lines(paragraphs)
    cleaned = [_clean_fragment(p, drop) for p in paragraphs]
    text, segments = _assemble(
        [(fragment, None, index + 1) for index, fragment in enumerate(cleaned)]
    )
    return ExtractedDocument(
        text=text,
        total_pages=1,
        segments=segments,
        locator_kind="paragraph" if segments else "none",
    )


def extract_text(raw_bytes: bytes, mime_type: str) -> tuple[str, int]:
    """Backwards-compatible wrapper: ``(cleaned text, total_pages)``.

    Prefer :func:`extract_document` when provenance is needed (ingest does).
    """
    document = extract_document(raw_bytes, mime_type)
    return document.text, document.total_pages


# ---------------------------------------------------------------------------
# Format-specific loaders (return raw fragments, no joining)
# ---------------------------------------------------------------------------


def _pdf_pages(raw_bytes: bytes) -> list[str]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(raw_bytes))
    return [(page.extract_text() or "") for page in reader.pages]


def _docx_paragraphs(raw_bytes: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(raw_bytes)) as docx:
        xml = docx.read("word/document.xml")
    root = ET.fromstring(xml)
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs: list[str] = []
    for paragraph in root.findall(".//w:p", namespace):
        text = "".join(node.text or "" for node in paragraph.findall(".//w:t", namespace))
        if text.strip():
            paragraphs.append(text)
    return paragraphs

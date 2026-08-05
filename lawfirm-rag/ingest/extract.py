from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET


def clean_text(text: str) -> str:
    """Clean extraction artifacts common in PDFs and legal documents."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    counts: dict[str, int] = {}
    for line in lines:
        stripped = line.strip()
        if stripped:
            counts[stripped] = counts.get(stripped, 0) + 1

    without_repeated = [
        line
        for line in lines
        if not line.strip() or counts.get(line.strip(), 0) < 3
    ]
    cleaned = "\n".join(without_repeated)

    cleaned = re.sub(r"(\w)-\n(\w)", r"\1 \2", cleaned)
    cleaned = re.sub(r"(?im)^\s*(?:\d+|page\s+\d+\s+of\s+\d+|-\s*\d+\s*-)\s*$", "", cleaned)
    cleaned = re.sub(r"[ \t\f\v]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def extract_text(raw_bytes: bytes, mime_type: str) -> tuple[str, int]:
    """Extract cleaned plain text and page count from raw file bytes."""
    if mime_type == "application/pdf":
        text, total_pages = _extract_pdf(raw_bytes)
        return clean_text(text), total_pages
    if mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        return clean_text(_extract_docx(raw_bytes)), 1
    # Google Docs exported as text/plain, and any other text type
    return clean_text(raw_bytes.decode("utf-8", errors="replace")), 1


def _extract_pdf(raw_bytes: bytes) -> tuple[str, int]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(raw_bytes))
    pages = []
    for page in reader.pages:
        text = page.extract_text() or ""
        pages.append(text)
    return "\n\n".join(pages), len(reader.pages)


def _extract_docx(raw_bytes: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(raw_bytes)) as docx:
        xml = docx.read("word/document.xml")
    root = ET.fromstring(xml)
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs = []
    for paragraph in root.findall(".//w:p", namespace):
        text = "".join(node.text or "" for node in paragraph.findall(".//w:t", namespace))
        if text.strip():
            paragraphs.append(text)
    return "\n\n".join(paragraphs)

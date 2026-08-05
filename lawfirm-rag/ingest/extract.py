from __future__ import annotations

import io
import zipfile
import xml.etree.ElementTree as ET


def extract_text(raw_bytes: bytes, mime_type: str) -> str:
    """Extract plain text from raw file bytes."""
    if mime_type == "application/pdf":
        return _extract_pdf(raw_bytes)
    if mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        return _extract_docx(raw_bytes)
    # Google Docs exported as text/plain, and any other text type
    return raw_bytes.decode("utf-8", errors="replace")


def _extract_pdf(raw_bytes: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(raw_bytes))
    pages = []
    for page in reader.pages:
        text = page.extract_text() or ""
        pages.append(text)
    return "\n\n".join(pages)


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

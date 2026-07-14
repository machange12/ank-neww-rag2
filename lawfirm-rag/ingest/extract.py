from __future__ import annotations

import io


def extract_text(raw_bytes: bytes, mime_type: str) -> str:
    """Extract plain text from raw file bytes."""
    if mime_type == "application/pdf":
        return _extract_pdf(raw_bytes)
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

"""Conservative text normalization for citation verification.

What this does:
  * Unicode NFC (conservative — does not fold away meaningful distinctions);
  * typography/quote normalization (curly quotes, dashes, NBSP -> ASCII);
  * whitespace collapse (runs -> single space, strip edges);
  * controlled case folding (ASCII letters only — non-ASCII script is never
    folded, so script that could carry legal meaning is preserved);
  * optional header/footer removal for sourced text.

What this preserves:
  * the ORIGINAL text (verbatim) and a best-effort character offset map
    (normalized index -> original index) for reproducible span evidence.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class NormalizedText:
    """Original text + conservative normalization + character offsets.

    ``offsets[i]`` is the index into ``original`` of the character that produced
    ``normalized[i]`` (best-effort for 1:1 stages and whitespace runs).
    """

    original: str
    normalized: str
    offsets: tuple[int, ...]

    def span(self, start: int, end: int) -> tuple[int, int]:
        """Map a normalized span back to original text offsets."""
        if not self.offsets:
            return (0, len(self.original))
        s = self.offsets[start] if 0 <= start < len(self.offsets) else len(self.original)
        e = self.offsets[end - 1] + 1 if 0 <= end - 1 < len(self.offsets) else len(self.original)
        return (s, max(s, e))


_UNICODE_FORM = "NFC"

# Curly/smart quotes, dashes, non-breaking and narrow spaces -> ASCII.
_TYPOGRAPHIC = {
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u00ab": '"', "\u00bb": '"',
    "\u2013": "-", "\u2014": "-",
    "\u00a0": " ", "\u2009": " ", "\u200a": " ", "\u200b": "",
    "\u2028": "\n", "\u2029": "\n",
}
_TYPOGRAPHIC_RE = re.compile("|".join(map(re.escape, _TYPOGRAPHIC)))
WHITESPACE_RE = re.compile(r"\s+")
_EDGE_WS_RE = re.compile(r"^\s+|\s+$")
_ASCII_UPPER_RE = re.compile(r"[A-Z]")


def _fold_typography(text: str) -> str:
    return _TYPOGRAPHIC_RE.sub(lambda m: _TYPOGRAPHIC[m.group(0)], text)


def _ascii_casefold(text: str) -> str:
    # Controlled case folding: ASCII letters only.
    return _ASCII_UPPER_RE.sub(lambda m: m.group(0).lower(), text)


def _is_space(ch: str) -> bool:
    return ch.isspace()


def _matches(original_char: str, normalized_char: str) -> bool:
    # Match after typographic folding + ASCII case folding + NFC.
    if original_char == normalized_char:
        return True
    folded = _ascii_casefold(unicodedata.normalize(_UNICODE_FORM, _fold_typography(original_char)))
    return folded == normalized_char


def _build_offset_map(original: str, normalized: str) -> tuple[int, ...]:
    """Best-effort normalized-index -> original-index mapping."""
    offsets: list[int] = []
    oi = 0
    orig_len = len(original)
    for ch in normalized:
        if ch == " ":
            # Whitespace run in the original collapses to a single space;
            # map to the end of the preceding run position.
            while oi < orig_len and _is_space(original[oi]):
                oi += 1
            offsets.append(oi)
            continue
        while oi < orig_len:
            if _matches(original[oi], ch):
                offsets.append(oi)
                oi += 1
                break
            oi += 1
        else:
            offsets.append(oi)
    return tuple(offsets)


def normalize_text(
    text: str,
    *,
    case_fold: bool = True,
    strip_headers_footers: bool = False,
    header_footer_lines: int = 1,
) -> NormalizedText:
    """Normalize ``text`` conservatively and preserve the original + offsets."""
    original = text or ""
    working = _fold_typography(original)
    working = unicodedata.normalize(_UNICODE_FORM, working)

    if strip_headers_footers and header_footer_lines > 0:
        lines = working.splitlines()
        if len(lines) > 2 * header_footer_lines:
            working = "\n".join(lines[header_footer_lines:-header_footer_lines])

    working = WHITESPACE_RE.sub(" ", working)
    working = _EDGE_WS_RE.sub("", working)

    if case_fold:
        working = _ascii_casefold(working)

    return NormalizedText(
        original=original,
        normalized=working,
        offsets=_build_offset_map(original, working),
    )
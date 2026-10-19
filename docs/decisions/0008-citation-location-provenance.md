# ADR-0008 — Citation Location Provenance

**Status:** Accepted (2026)

**Context.** The sources panel showed a document title and, for PDFs, a page
number. That page number was not measured: `metadata.page_number` was computed
as `chunk_index // chunks_per_page`, a guess derived from how many chunks the
splitter happened to produce, and it was `0` for every DOCX and plain-text file.
A citation therefore looked precise while pointing at nothing, which is worse
than no locator at all for a lawyer checking a passage.

**Decision.**

1. **Locators are derived, never computed from chunk counts.** `ingest/extract.py`
   now returns an `ExtractedDocument` whose `segments` address spans of the
   extracted text: one per PDF page, one per non-empty DOCX/text paragraph.
   `ingest/embed.py` maps each chunk back onto those spans via character offsets
   and records `page_start`/`page_end` (or `paragraph_start`/`paragraph_end`) plus
   `locator_kind`. A chunk that straddles a boundary honestly reports a range
   (`pp. 12–13`) rather than picking one end. `CHUNKING_VERSION` is bumped to
   `…-v3` so mixed old/new provenance is detectable.
2. **DOCX gets paragraph numbers, not pages.** Word paginates at render time, so
   a stored page number for a `.docx` would be an invention. Paragraph numbers are
   the honest, reproducible locator.
3. **No locator beats a wrong one.** When a document carries no real provenance
   (a file ingested before this change), the source shows no page at all. Legacy
   `page_number` is still read as a fallback alias, but `0` is treated as
   "unknown", and `scripts/reingest_provenance.py` strips the synthetic value from
   existing upload chunks while re-ingesting Drive-sourced files to backfill real
   spans.
4. **The answer keeps plain `[n]`; the interface carries the location.** The
   model cites only the source number, so it cannot invent a page. The locator,
   the aggregated page/paragraph range for the document, and a short verbatim
   quote (whitespace-normalised, word-boundary trimmed) are attached to each
   source server-side and rendered in the sources panel, in the Draft Studio
   source list, and in DOCX endnotes. The prompt does permit a model to repeat a
   location it was shown in a chunk header, but forbids stating, estimating or
   inferring one that is not there.
5. **The in-app viewer marks the locators.** `/documents/view` prefixes each chunk
   with a `===== p. 12 =====`-style marker so a reader following a citation can
   find the passage in the reassembled text. The marker is labelled as the
   *chunk's* locator, not a true page break, because a chunk may span one.

**Consequences.** Citations are checkable: page ranges come from the source
document, and every quoted excerpt is verbatim from the retrieved chunk. The cost
is that re-ingesting is required once to populate provenance (done by
`scripts/reingest_provenance.py`), and UI uploads ingested before the change
cannot be repaired in place because their original bytes were not retained — they
must be re-uploaded, and until then they show no page (R5 in the threat model).

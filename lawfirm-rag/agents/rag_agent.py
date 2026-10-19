from __future__ import annotations

import logging
import asyncio
import json
import re
import time
from functools import lru_cache
from typing import Any, List

from langchain_cohere import CohereRerank
from langchain_community.vectorstores import SupabaseVectorStore
from langchain_core.retrievers import BaseRetriever
from langchain_core.documents import BaseDocumentCompressor, Document
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_classic.retrievers import ContextualCompressionRetriever
from langchain_classic.retrievers.multi_query import MultiQueryRetriever

from search.hybrid import hybrid_search
from sessions.supabase_history import SupabaseChatHistory
from config import settings
from providers import make_chat_llm, get_embeddings

logger = logging.getLogger(__name__)

RAG_STREAM_TEMPLATE = """You are a secure legal knowledge assistant for Anjarwalla & Khanna Advocates.

CORE BEHAVIOUR RULES:
1. Answer ONLY from the retrieved context below — never from general knowledge.
2. For greetings or small talk: respond briefly without referencing the knowledge base.
3. Cite every statement drawn from the context with its source number in square brackets, e.g. [1] or [2][3], using ONLY the numbers shown in the RETRIEVED CONTEXT headers. Never cite file names, matter IDs or numbers that do not appear there.
4. If the context does not answer the question, say: "I could not find an answer in the firm's document repository." Then ask ONE short follow-up question that would let you find it (e.g. the case name or parties, the matter ID, or the document type). If the context mentions documents that might be relevant, name them so the user can pick. Do NOT include any [n] citation markers in this reply.
5. If the question is ambiguous — it fits more than one document in the context (e.g. "the succession case" when there are several), or it refers to "the judge"/"the contract" with no earlier turn saying which — do not guess. Briefly list the candidates from the context and ask which one the user means.
6. If you can answer only part of the question, answer that part, then say what is missing and ask a follow-up question about it.
7. Never speculate beyond the retrieved content.
8. Write in plain text only — no markdown. Do not use asterisks for bold or italic, dash or bullet lists, or # headings. Use complete sentences and short paragraphs.
9. A context header may show a page (p. 12 / pp. 12–13) or paragraph (¶ 4) for the passage. You may repeat that location when you cite the passage, but never state, estimate or infer a page or paragraph number that is not shown in the header of the chunk you are citing. The interface displays each citation's exact location from the source list.

RETRIEVED CONTEXT:
{context}
"""

NO_RESULT_RESPONSE = (
    "I could not find an answer in the firm's document repository. "
    "Could you give me a bit more to go on — the case name or parties, the matter ID, "
    "or the type of document (judgment, contract, memo)?"
)
OUT_OF_SCOPE_RESPONSE = "This question is outside the scope of the firm's document repository."
# Distinct from NO_RESULT_RESPONSE: relevant documents WERE found, but the
# answer-generation call itself failed (rate limit, timeout, provider
# outage, etc). Previously both cases returned NO_RESULT_RESPONSE, so a
# genuine outage/rate-limit was indistinguishable in the UI from "nothing
# relevant is in the document repository" — see the run_chat/stream_chat
# LLM invoke except-blocks below.
GENERATION_FAILED_RESPONSE = (
    "I found relevant documents but could not generate an answer right now "
    "(the answer service is temporarily unavailable or rate-limited). Please try again shortly."
)

# Query-intent → retrieval depth. shorter depths for point questions,
# deeper recalls for summaries/comparisons across the corpus.
INTENT_RETRIEVE_K = {
    "factual": 8,
    "comparative": 15,
    "summarization": 20,
    "procedural": 10,
    "out_of_scope": 0,
}

# Intents where MultiQueryRetriever pays for itself. Point-lookup queries
# (factual, procedural) rarely gain recall from LLM-generated rewrites and
# eat 2-4s per query on the extra LLM call + 3-4× retrieval RPCs.
MULTI_QUERY_INTENTS = {"comparative", "summarization"}

# Keyword rules → intent (English + Swahili). Whole-word, case-insensitive,
# first hit wins, default "factual". Replaced an LLM classifier whose prompt
# never listed the categories: it invented labels ("case_law") so every query
# fell to factual, its made-up retrieve_k (5) starved the reranker, and it
# cost ~3s/query. out_of_scope is not keyword-detectable; the answer prompt's
# rule 2 handles small talk.
# ponytail: keyword heuristic, misses paraphrases ("how do X and Y stack up").
# Upgrade to a small embedding classifier once a golden set exists to score it.
INTENT_PATTERNS: dict[str, re.Pattern[str]] = {
    "comparative": re.compile(
        r"\b(compare[sd]?|comparison|differences?|differ|contrast|distinguish\w*|similarit\w+"
        r"|linganisha|tofauti)\b", re.I),
    "summarization": re.compile(
        r"\b(summar\w+|overview|outline|key points|main points|list all|all (the )?cases"
        r"|muhtasari|fupisha)\b", re.I),
    "procedural": re.compile(
        r"\b(how (do|can|to)|procedure|process for|steps?|requirements? for|apply for|file an?"
        r"|jinsi ya|hatua|utaratibu)\b", re.I),
}

# Keyword hints → doc_type. Whole-word, case-insensitive; first hit wins.
# Substring matching used to send "summarise the facts" ("act") to the
# statute filter.
DOC_TYPE_HINT_PATTERNS: dict[str, re.Pattern[str]] = {
    # "judgement" is the British spelling — Kenyan legal writing uses both.
    # "estate", "petition", "kehc"/"kesc"/"keca" (High/Supreme/Appeal court
    # neutral citations) added because succession queries never say "judgment".
    "judgment": re.compile(
        r"\b(judge?ments?|rulings?|decisions?|case|eklr|estate|petition|kehc|kesc|keca|vs?\.?)(?!\w)", re.I),
    "contract": re.compile(r"\b(contracts?|agreements?|clauses?|nda|lease)\b", re.I),
    "statute": re.compile(r"\b(act|statutes?|regulations?|section|cap\.)(?!\w)", re.I),
}


def _doc_type_hint_from_query(query: str) -> str | None:
    """
    Cheap, LLM-free doc_type hint for a query.

    Returns "judgment", "contract", "statute" or None. A hint, not a hard
    filter — HybridRetriever retries unfiltered when the filtered pass is empty.
    """
    for doc_type, pattern in DOC_TYPE_HINT_PATTERNS.items():
        if pattern.search(query):
            return doc_type
    return None


def classify_intent(query: str) -> dict[str, Any]:
    """Pick intent + retrieval depth + doc_type hint from keyword rules. No LLM call."""
    intent = next((name for name, p in INTENT_PATTERNS.items() if p.search(query)), "factual")
    return {
        "intent": intent,
        "retrieve_k": INTENT_RETRIEVE_K[intent],
        "doc_type_hint": _doc_type_hint_from_query(query),
    }


def _make_vector_store(user_client: Any) -> SupabaseVectorStore:
    embeddings = get_embeddings()
    return SupabaseVectorStore(
        client=user_client,
        embedding=embeddings,
        table_name="documents",
        query_name="match_documents_rls",  # security invoker — RLS applies
    )


class HybridRetriever(BaseRetriever):
    """Retriever that tries a hybrid (vector + keyword) RPC first and falls back to vector similarity."""

    user_client: Any
    store: Any
    k: int | None = None
    doc_type_filter: str | None = None
    # Which path served the most recent call: "hybrid" or "vector_fallback".
    # A real Pydantic field (not a plain attribute) since BaseRetriever is a
    # Pydantic model and rejects undeclared attributes.
    last_path: str = ""

    class Config:
        arbitrary_types_allowed = True

    def _get_relevant_documents(self, query: str) -> List[Document]:
        top_k = self.k if self.k is not None else settings.retrieve_top_k
        # doc_type_filter is a keyword guess; if it matches nothing (e.g.
        # "statute" with no statutes ingested) retry unfiltered rather than
        # returning zero docs.
        for doc_type_filter in dict.fromkeys([self.doc_type_filter, None]):
            try:
                docs = hybrid_search(
                    self.user_client,
                    query,
                    match_count=top_k,
                    doc_type_filter=doc_type_filter,
                )
            except Exception:
                docs = []
            if docs:
                self.last_path = "hybrid" if doc_type_filter == self.doc_type_filter else "hybrid_unfiltered"
                return docs
        # Fallback to vector similarity, unfiltered — both hybrid passes
        # (filtered + unfiltered) already came back empty. hybrid_search() already logs a warning on outright RPC failure; this
        # covers the equally-relevant "RPC succeeded but returned zero rows"
        # case, which was previously invisible — no log at all.
        logger.warning(
            "hybrid retrieval returned no rows, falling back to vector-only "
            "similarity search | query=%r top_k=%d doc_type_filter=%r",
            query,
            top_k,
            self.doc_type_filter,
        )
        self.last_path = "vector_fallback"
        return self.store.similarity_search(query, k=top_k)


class _FailOpenRerank(BaseDocumentCompressor):
    """Wraps a hosted reranker so an API error (rate limit, outage) degrades
    to the hybrid RRF order instead of failing retrieval — which would
    otherwise surface as the zero-doc NO_RESULT_RESPONSE."""

    inner: Any
    top_n: int = 5
    # After a failure, skip the API for a while so an outage costs one
    # timeout, not one per question.
    skip_until: float = 0.0

    class Config:
        arbitrary_types_allowed = True

    def compress_documents(self, documents, query, callbacks=None):
        documents = list(documents)
        ranked = documents[: self.top_n]
        if time.monotonic() >= self.skip_until:
            try:
                ranked = list(self.inner.compress_documents(documents, query, callbacks=callbacks))
            except Exception as exc:  # noqa: BLE001
                self.skip_until = time.monotonic() + 120
                logger.warning("reranker failed, using unreranked RRF order for 120s | %s", exc)
        return _prefer_named_document(query, documents, ranked, self.top_n)


@lru_cache(maxsize=1)
def _cohere_reranker() -> "_FailOpenRerank":
    # Cached: CohereRerank() costs ~1s of CPU to build (client/SSL setup).
    # timeout: the SDK default is 300s — a hung rerank call stalled whole
    # chats; 4s (normal rerank <1s) then fail-open to RRF order.
    import cohere
    return _FailOpenRerank(inner=CohereRerank(
        client=cohere.ClientV2(api_key=settings.cohere_api_key, timeout=4),
        cohere_api_key=settings.cohere_api_key,
        top_n=5,
        model="rerank-v3.5",
    ))


def warm_clients() -> None:
    get_embeddings()
    make_chat_llm(streaming=False)
    make_chat_llm(streaming=True)
    if settings.cohere_api_key:
        _cohere_reranker()


def _make_retriever(
    user_client: Any,
    llm: Any,
    k: int | None = None,
    doc_type_filter: str | None = None,
    use_multi_query: bool = True,
) -> tuple[Any, "HybridRetriever"]:
    """Build the retrieval chain; ``k`` overrides settings.retrieve_top_k for this call.

    ``doc_type_filter`` (optional) restricts retrieval to a single document type
    (e.g. "judgment", "contract", "statute"). Passing None preserves the existing
    unfiltered behaviour.

    Returns ``(retriever, hybrid_retriever)`` — the inner ``HybridRetriever`` is
    exposed alongside the (possibly multi-query/reranked-wrapped) outer
    retriever so callers can read ``hybrid_retriever.last_path`` after
    invocation for retrieval-quality logging (was previously untracked).
    """
    if k is None:
        k = settings.retrieve_top_k
    store = _make_vector_store(user_client)

    # Use the hybrid retriever that prefers the RPC-based fusion search
    hybrid_retriever = HybridRetriever(
        user_client=user_client,
        store=store,
        k=k,
        doc_type_filter=doc_type_filter,
    )

    # Wrap with multi-query rewriting to broaden retrievals — but only when
    # the intent asked for it. MultiQuery = 1 LLM rewrite call + 3-4× the
    # retrieval RPCs (~2-4s tax per query). Point/direct lookups (factual,
    # procedural) don't benefit; comparative/summarization queries do.
    if use_multi_query:
        multi_retriever = MultiQueryRetriever.from_llm(
            retriever=hybrid_retriever,
            llm=llm,
            include_original=True,
        )
    else:
        multi_retriever = hybrid_retriever

    # Reranker: prefer Cohere's hosted API (~300ms) when a key is set —
    # the local CPU cross-encoder measured ~3.4s/pair (25 pairs = 82s).
    # rerank-v3.5 is multilingual (English + Swahili). Local CE is the
    # no-key fallback, then no reranker at all.
    compressor = None
    if settings.cohere_api_key:
        compressor = _cohere_reranker()
    else:
        try:
            from rerank.cross_encoder import LocalCrossEncoderReranker
            compressor = LocalCrossEncoderReranker(top_n=5)
        except Exception as exc:
            logger.warning("local cross-encoder unavailable, no reranker | %s", exc)

    if compressor is not None:
        return ContextualCompressionRetriever(
            base_compressor=compressor,
            base_retriever=multi_retriever,
        ), hybrid_retriever

    return multi_retriever, hybrid_retriever


def _doc_key(meta: dict[str, Any]) -> str:
    """One key per source document, shared by the context numbering and the
    sources list so citation [n] always means sources[n-1]."""
    return meta.get("file_id") or meta.get("url") or meta.get("file_url") or meta.get("file_title") or meta.get("title") or ""


def _build_context_string(docs: list[Any], sources: list[dict[str, Any]]) -> str:
    """Context for the prompt. Chunks are labelled with their SOURCE number
    (several chunks of one document share a number), matching the numbered
    sources list the user sees."""
    if not docs:
        return "No relevant documents found."
    number = {s["key"]: s["n"] for s in sources}
    parts = []
    for doc in docs:
        meta = getattr(doc, "metadata", {}) or {}
        source = sources[number.get(_doc_key(meta), 1) - 1]
        header = f"[{source['n']}] {source['title']}"
        if meta.get("matter_id"):
            header += f" | Matter: {meta['matter_id']}"
        if meta.get("section_heading"):
            header += f" | {meta['section_heading']}"
        location = _location_from_meta(meta)
        if location:
            header += f" | {location['label']}"
        parts.append(f"{header}\n{doc.page_content}")
    return "\n\n---\n\n".join(parts)


_CITATION = re.compile(r"\[(\d+)\]")
# gpt-oss often emits its native citation style 【1】 / 【1†L1-L3】 instead of
# the [1] the prompt asks for.
_NATIVE_CITATION = re.compile(r"【\s*(\d+)(?:†[^】]*)?\s*】")


def _normalize_citations(answer: str) -> str:
    return _NATIVE_CITATION.sub(r"[\1]", answer)


def _strip_invalid_citations(answer: str, n_sources: int) -> tuple[str, int]:
    """Remove citation markers that point at no source ([7] when there are 3).
    Returns the cleaned answer and how many were removed."""
    removed = 0

    def keep(m: re.Match[str]) -> str:
        nonlocal removed
        if 1 <= int(m[1]) <= n_sources:
            return m[0]
        removed += 1
        return ""

    return _CITATION.sub(keep, answer), removed


# The canonical no-answer opening (prompt rule 4). A reply that starts with
# this admits nothing in the context supports an answer, yet the model used to
# still tack on valid-index citations ([1][2]) — which _strip_invalid_citations
# keeps, so the user saw fabricated citations. Same phrase the golden-set eval
# keys on (scripts/eval_golden.py).
_NO_ANSWER = re.compile(
    r"^\s*I could not find an answer in the firm'?s document repository",
    re.I,
)


def _is_no_answer(answer: str) -> bool:
    return bool(_NO_ANSWER.match(answer))


# gpt-oss writes markdown (**bold**, - bullets, # headings) but the UI renders
# answers as plain text, so the symbols showed literally. Strip them rather
# than render markdown — answers should read as normal prose.
_MD_HEADING = re.compile(r"^[ \t]*#{1,6}[ \t]*", re.M)
_MD_BULLET = re.compile(r"^[ \t]*[-*•][ \t]+", re.M)
_MD_BOLD = re.compile(r"(\*\*|__)(.+?)\1", re.S)
_MD_ITALIC = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", re.S)


def _strip_markdown(answer: str) -> str:
    answer = _MD_HEADING.sub("", answer)
    answer = _MD_BULLET.sub("", answer)
    answer = _MD_BOLD.sub(r"\2", answer)
    answer = _MD_ITALIC.sub(r"\1", answer)
    # Any stray single asterisks left over (lone emphasis markers, list dots).
    return answer.replace("*", "")


# Definite references that presuppose the user has ONE document in mind.
_DEFINITE_REF = re.compile(
    r"\b(the|this|that)\s+(judge|court|case|contract|agreement|petitioner|applicant|respondent|"
    r"appellant|parties|party|matter|ruling|judgment|lease|deceased|plaintiff|defendant|accused|"
    r"tenant|landlord|employee|employer|client|letter|affidavit|policy|nda|grant|appeal)\b",
    re.I,
)
# Title words too common to identify a document on their own.
_GENERIC_TITLE_WORDS = {
    "the", "and", "of", "estate", "agreement", "case", "policy", "judgment", "judgments", "republic",
    "court", "high", "civil", "supreme", "letter", "client", "legal", "doc", "pdf", "docx", "kehc", "kesc", "keca",
}
_KEEP_LOWER = {"v", "of", "the", "and", "in", "re"}
# Capitalised words that are not names of a specific document or party.
_COMMON_CAPS = {"court", "judge", "high", "appeal", "supreme", "republic", "kenya", "kenyan", "act", "section", "article", "constitution", "the"}


def _pretty_title(file_title: str) -> str:
    """"Judgments/2026-kehc-12951-mutunga-v-republic.pdf" -> "Mutunga v Republic [2026 KEHC 12951]",
    "08_Commercial_Lease_Agreement.docx" -> "Commercial Lease Agreement"."""
    name = re.sub(r"\.\w+$", "", file_title.rsplit("/", 1)[-1])
    cite = ""
    m = re.match(r"(\d{4})-([a-z]+)-(\d+)-(.+)", name)
    if m:
        cite, name = f" [{m[1]} {m[2].upper()} {m[3]}]", m[4]
    name = re.sub(r"^(synth_|doc)?\d+_", "", name)
    words = [w for w in re.split(r"[-_\s]+", name) if w and not re.fullmatch(r"\(\d+\)", w)]
    words = [w if w.lower() in _KEEP_LOWER else (w.upper() if len(w) <= 3 else w.capitalize()) for w in words]
    return " ".join(words) + cite


def _query_names_title(query: str, pretty_title: str) -> bool:
    """True when the question uses a distinctive word of the document's title
    ("Chebet" for "Chebet v Republic", "Westlands" for the Westlands lease)."""
    # Letters only: years and citation numbers ("2026", "12838") are shared by
    # many documents, so "HC-245-2026" must not match "[2026 KEHC 12838]".
    title_words = set(re.findall(r"[a-z]+", pretty_title.lower())) - _GENERIC_TITLE_WORDS
    query_words = set(re.findall(r"[a-z]+", query.lower()))
    return bool({w for w in title_words if len(w) >= 3} & query_words)


def _prefer_named_document(query: str, candidates: list[Any], ranked: list[Any], top_n: int) -> list[Any]:
    """When the question names a document, its chunks take the context slots
    before other documents. The reranker scores chunks on their own, so for
    "What was the outcome of Chebet v Republic?" it kept other 'v Republic'
    appeals and dropped Chebet's own disposition chunk; the model then
    reported the lower court's result as the outcome."""
    def named(doc: Any) -> bool:
        title = (getattr(doc, "metadata", {}) or {}).get("file_title") or ""
        return bool(title) and _query_names_title(query, _pretty_title(title))

    def key(doc: Any) -> tuple:
        meta = getattr(doc, "metadata", {}) or {}
        return (_doc_key(meta), meta.get("chunk_index"), doc.page_content[:80])

    pinned: dict[tuple, Any] = {}
    for doc in [*ranked, *candidates]:  # reranker order first, then search order
        if named(doc):
            pinned.setdefault(key(doc), doc)
    if not pinned:
        return ranked[:top_n]
    rest = [d for d in ranked if key(d) not in pinned]
    return [*pinned.values(), *rest][:top_n]


def _clarifying_question(query: str, docs: list[Any]) -> str | None:
    """Ask which document is meant instead of guessing, when the question
    points at one specific document ("the judge", "the contract") without
    naming it and the search hits several. Deterministic: the LLM, given
    mostly-one-document context, reliably guessed instead of asking.
    """
    # ponytail: keyword heuristic (definite reference + title-word overlap).
    # Misses unnamed paraphrases; over-asks if a party is named only by a
    # word absent from the file name. Upgrade: let the LLM decide with a
    # stronger prompt once a labelled ambiguity set exists.
    if not _DEFINITE_REF.search(query):
        return None
    # A capitalised name the user typed ("Bahari Shipping") that matches no
    # candidate means they asked about a specific document we may not hold:
    # let the answer step say so instead of offering unrelated documents.
    names = {w.lower() for w in re.findall(r"(?<!^)(?<![.?!]\s)\b[A-Z][a-z]{2,}\b", query)} - _COMMON_CAPS
    candidates: dict[str, None] = {}
    for doc in docs:
        title = (getattr(doc, "metadata", {}) or {}).get("file_title") or ""
        if title:
            candidates.setdefault(_pretty_title(title), None)
    if len(candidates) < 2:
        return None
    title_text = " ".join(candidates).lower()
    if names and not any(n in title_text for n in names):
        return None
    if any(_query_names_title(query, title) for title in candidates):
        return None  # the question names one of the documents
    listed = "\n".join(f"- {t}" for t in list(candidates)[:5])
    return f"Your question could refer to more than one document:\n{listed}\n\nWhich one do you mean?"


# Longest verbatim excerpt shown under a source. Long enough to let a reader
# recognise the passage, short enough that the source list stays scannable.
_QUOTE_MAX = 320


def _locator_label(
    page_start: int | None,
    page_end: int | None,
    paragraph_start: int | None,
    paragraph_end: int | None,
) -> str:
    """Human label for a retrieved span: "p. 12", "pp. 12–13", "¶ 4", "¶¶ 4–6".

    Pages win over paragraphs when a document somehow carries both (a PDF
    chunk is always page-located). Empty string when the document has no
    reliable locator — we show nothing rather than a number we cannot stand
    behind.
    """
    if page_start:
        end = page_end or page_start
        return f"p. {page_start}" if end == page_start else f"pp. {page_start}–{end}"
    if paragraph_start:
        end = paragraph_end or paragraph_start
        return f"¶ {paragraph_start}" if end == paragraph_start else f"¶¶ {paragraph_start}–{end}"
    return ""


def _location_from_meta(meta: dict[str, Any]) -> dict[str, Any] | None:
    """Chunk-level locator, or None when the chunk has no real provenance.

    ``page_number`` is the pre-provenance alias; it was 0 for DOCX/TXT and a
    synthetic guess for PDFs, so 0 is treated as "unknown". Documents ingested
    before the provenance change therefore report no page until re-ingested
    (scripts/reingest_provenance.py) — a missing page is honest, a wrong one
    is not.
    """
    page_start = meta.get("page_start") or meta.get("page_number") or None
    page_end = meta.get("page_end") or page_start
    paragraph_start = meta.get("paragraph_start") or None
    paragraph_end = meta.get("paragraph_end") or paragraph_start
    if not page_start and not paragraph_start:
        return None
    return {
        "chunk_index": meta.get("chunk_index", 0) or 0,
        "page_start": page_start,
        "page_end": page_end,
        "paragraph_start": paragraph_start,
        "paragraph_end": paragraph_end,
        "label": _locator_label(page_start, page_end, paragraph_start, paragraph_end),
    }


def _quote_from(text: str) -> str:
    """Short verbatim excerpt from the retrieved chunk, whitespace-normalised."""
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= _QUOTE_MAX:
        return cleaned
    cut = cleaned.rfind(" ", 0, _QUOTE_MAX)
    return cleaned[: cut if cut > 0 else _QUOTE_MAX].rstrip(" ,;:.") + "…"


def _sources_from_docs(docs: list[Any]) -> list[dict[str, Any]]:
    """Build source dicts with title/url/file_id plus chunk-level citation fields.

    Dedupe by (file_id, url, title) — whichever is available. Previously
    required a non-empty ``url`` to include a doc at all, which silently
    hid every uploaded-file source (UI-upload path stores no url, only
    Drive-ingested docs have one) even when the answer cited it.

    A source is listed once per document, but every retrieved chunk of that
    document contributes a *location* (page range / paragraph range) and the
    document-level range is the envelope of those, so an answer that leans on
    two pages of one file still cites [1] once and shows "pp. 12–14".
    """
    sources: list[dict[str, Any]] = []
    index: dict[str, int] = {}
    for doc in docs:
        meta = getattr(doc, "metadata", {}) or {}
        key = _doc_key(meta)
        if not key:
            continue

        location = _location_from_meta(meta)
        if key not in index:
            raw_title = meta.get("file_title") or meta.get("title") or "Unknown document"
            index[key] = len(sources)
            sources.append({
                "n": len(sources) + 1,  # the [n] the answer cites
                "key": key,
                "title": _pretty_title(raw_title),
                "file_title": raw_title,
                "url": meta.get("url") or meta.get("file_url", ""),
                "file_id": meta.get("file_id", ""),
                "kind": meta.get("kind", "") or "",
                "section_heading": meta.get("section_heading", "") or "",
                "chunk_index": meta.get("chunk_index", 0) or 0,
                "page_number": meta.get("page_start") or 0,
                "page_start": None,
                "page_end": None,
                "paragraph_start": None,
                "paragraph_end": None,
                "locator_kind": meta.get("locator_kind", "") or "",
                "label": "",
                "quote": _quote_from(getattr(doc, "page_content", "") or ""),
                "locations": [],
            })

        entry = sources[index[key]]
        if location and location not in entry["locations"]:
            entry["locations"].append(location)
        if not entry["quote"]:
            entry["quote"] = _quote_from(getattr(doc, "page_content", "") or "")
        # First chunk's heading wins; later chunks are continuations.
        if not entry["section_heading"] and meta.get("section_heading"):
            entry["section_heading"] = meta["section_heading"]

    for entry in sources:
        locations = entry["locations"]
        if not locations:
            continue
        entry["page_start"] = min((l["page_start"] for l in locations if l["page_start"]), default=None)
        entry["page_end"] = max((l["page_end"] for l in locations if l["page_end"]), default=None)
        entry["paragraph_start"] = min(
            (l["paragraph_start"] for l in locations if l["paragraph_start"]), default=None
        )
        entry["paragraph_end"] = max(
            (l["paragraph_end"] for l in locations if l["paragraph_end"]), default=None
        )
        entry["page_number"] = entry["page_start"] or 0
        entry["label"] = _locator_label(
            entry["page_start"],
            entry["page_end"],
            entry["paragraph_start"],
            entry["paragraph_end"],
        )
    return sources


def _log_retrieval(
    session_id: str,
    query: str,
    docs: list[Any],
    reranked: bool = False,
    intent: dict[str, Any] | None = None,
    path: str = "",
) -> None:
    """Log retrieval quality info for each query.

    One structured line per query carrying every retrieval-quality signal
    that already existed in the code but was previously either not logged at
    all (intent classification, hybrid-vs-vector-fallback path) or logged at
    a level nothing captured (see logging_setup.configure_logging) — makes it
    possible to grep/alert on zero-result rate, fallback rate, and intent
    distribution instead of guessing at retrieval quality.
    """
    top_chunks = []
    for doc in docs[:5]:
        meta = getattr(doc, "metadata", {}) or {}
        top_chunks.append({
            "file_id": meta.get("file_id", ""),
            "chunk_index": meta.get("chunk_index", ""),
            "section": meta.get("section_heading", ""),
        })
    logger.info(
        "retrieval | session=%s | query=%r | intent=%s | retrieve_k=%s | doc_type_hint=%s "
        "| path=%s | docs_retrieved=%d | zero_result=%s | cohere_reranked=%s | top_chunks=%s",
        session_id,
        query,
        (intent or {}).get("intent", ""),
        (intent or {}).get("retrieve_k", ""),
        (intent or {}).get("doc_type_hint", ""),
        path or "unknown",
        len(docs),
        len(docs) == 0,
        reranked,
        json.dumps(top_chunks),
    )


class _TTLCache:
    """Small in-process TTL cache (single Render instance, so no Redis)."""

    def __init__(self, ttl: float, maxsize: int) -> None:
        self.ttl, self.maxsize, self.data = ttl, maxsize, {}

    def get(self, key: Any) -> Any:
        hit = self.data.get(key)
        if hit and time.monotonic() - hit[0] < self.ttl:
            return hit[1]
        self.data.pop(key, None)
        return None

    def put(self, key: Any, value: Any) -> None:
        if len(self.data) >= self.maxsize:
            self.data.pop(next(iter(self.data)))  # oldest insert
        self.data[key] = (time.monotonic(), value)

    def clear(self) -> None:
        self.data.clear()


# Repeat questions answer instantly. Keyed per user (RLS + role prompt make
# answers user-specific) and on the conversation so far (follow-ups depend
# on it). Cleared on ingest; TTL bounds staleness if ingest runs elsewhere.
_ANSWER_CACHE = _TTLCache(ttl=600, maxsize=256)


def clear_answer_cache() -> None:
    _ANSWER_CACHE.clear()


def _answer_cache_key(user_id: str, chat_input: str, chat_history: list[Any]) -> tuple:
    norm = " ".join(chat_input.lower().split())
    return (user_id, norm, tuple(str(getattr(m, "content", m)) for m in chat_history))


def _retrieval_query(chat_input: str, chat_history: list[Any]) -> str:
    """Search text for this turn. A short reply after an earlier question
    ("the Ngeno one", "the 2025 judgment") usually answers the assistant's
    follow-up question, so search on the previous question plus the reply."""
    # ponytail: length heuristic; an LLM query-rewrite would catch long
    # follow-ups too, at +1 call per turn.
    if len(chat_input.split()) > 8:
        return chat_input
    previous = next((m.content for m in reversed(chat_history) if isinstance(m, HumanMessage)), "")
    return f"{previous}\n{chat_input}" if previous else chat_input


async def _prepare_turn(
    session_id: str,
    chat_input: str,
    user_client: Any,
    user_id: str,
    tenant_id: str | None,
    llm: Any,
) -> dict[str, Any]:
    """Everything before answer generation, shared by run_chat and stream_chat.

    Returns {"final": {...}} when the turn is answered without the LLM (cache
    hit, out of scope, nothing retrieved, clarifying question), otherwise
    {"messages", "sources", "cache_key", "persist", "t0", "t_retrieved"}.
    """
    t0 = time.perf_counter()

    # Load recent history (Supabase-backed) first: it feeds the answer-cache
    # key and the retrieval query for short follow-up replies. Reads run
    # through the caller's own client; RLS returns only their rows.
    history = SupabaseChatHistory(
        session_id=session_id,
        client=user_client,
        user_id=user_id,
        tenant_id=tenant_id,
    )
    try:
        chat_history: list[Any] = history.messages[-settings.context_window * 2:]
    except Exception as exc:  # noqa: BLE001
        logger.debug("history load failed: %s", exc)
        chat_history = []

    def persist(answer: str) -> None:
        # A persistence failure must not break the response.
        try:
            history.add_user_message(chat_input)
            history.add_ai_message(answer)
        except Exception as exc:  # noqa: BLE001
            logger.debug("memory persistence failed: %s", exc)

    cache_key = _answer_cache_key(user_id, chat_input, chat_history)
    cached = _ANSWER_CACHE.get(cache_key)
    if cached:
        persist(cached["answer"])
        logger.info("timing | session=%s | answer cache hit | total=%.2fs", session_id, time.perf_counter() - t0)
        return {"final": cached}

    intent = classify_intent(chat_input)
    if intent["intent"] == "out_of_scope":
        logger.info("out_of_scope query | session=%s | query=%r", session_id, chat_input)
        return {"final": {"answer": OUT_OF_SCOPE_RESPONSE, "sources": []}}

    retrieval_query = _retrieval_query(chat_input, chat_history)
    retriever, hybrid_retriever = _make_retriever(
        user_client,
        llm,
        k=intent["retrieve_k"],
        # No doc-type filter: labels come from an LLM classifier that can
        # fail (stored as "unknown"), and a filter then hides the right
        # document. Unfiltered hybrid scored 100% hit@1 on the golden set.
        use_multi_query=intent["intent"] in MULTI_QUERY_INTENTS,
    )
    docs: list[Any] = []
    try:
        # ainvoke: MultiQuery runs its rewritten queries concurrently.
        docs = await retriever.ainvoke(retrieval_query)
        if isinstance(docs, list):
            _log_retrieval(
                session_id=session_id,
                query=retrieval_query,
                docs=docs,
                reranked=bool(settings.cohere_api_key),
                intent=intent,
                path=hybrid_retriever.last_path,
            )
    except Exception as exc:
        # A retrieval-pipeline failure leads straight into the hallucination
        # guard below (silent "no answer"), so this must be visible.
        logger.warning("retrieval failed | session=%s | query=%r | %s", session_id, retrieval_query, exc, exc_info=True)

    # Hallucination guard: never call the LLM when nothing was retrieved.
    if not docs:
        logger.info("zero-doc retrieval | session=%s | query=%r", session_id, retrieval_query)
        persist(NO_RESULT_RESPONSE)
        return {"final": {"answer": NO_RESULT_RESPONSE, "sources": []}}

    sources = _sources_from_docs(docs)

    # First turn that says "the judge"/"the contract" without naming which,
    # while the hits span several documents: ask instead of guessing. Later
    # turns have history to disambiguate, and broad intents (comparisons,
    # summaries) legitimately span documents.
    if not chat_history and intent["intent"] in ("factual", "procedural"):
        clarify = _clarifying_question(chat_input, docs)
        if clarify:
            persist(clarify)
            logger.info("timing | session=%s | clarifying question | total=%.2fs", session_id, time.perf_counter() - t0)
            return {"final": {"answer": clarify, "sources": sources}}

    system_text = RAG_STREAM_TEMPLATE.format(context=_build_context_string(docs, sources))
    messages: list[Any] = [SystemMessage(content=system_text), *chat_history, HumanMessage(content=chat_input)]
    return {
        "messages": messages,
        "sources": sources,
        "cache_key": cache_key,
        "persist": persist,
        "t0": t0,
        "t_retrieved": time.perf_counter(),
    }


def _finish_turn(session_id: str, turn: dict[str, Any], answer: str) -> dict[str, Any]:
    answer = _normalize_citations(answer)
    if _is_no_answer(answer):
        # Nothing in the context supports an answer: drop any citation markers
        # the model still emitted and stop advertising the retrieved documents
        # as sources (they do not actually back the reply).
        answer = _CITATION.sub("", answer)
        answer = re.sub(r"[ \t]{2,}", " ", answer).strip()
        turn["sources"] = []
    else:
        answer = _strip_markdown(answer)
        answer, bad = _strip_invalid_citations(answer, len(turn["sources"]))
        if bad:
            logger.warning("invalid citations removed | session=%s | count=%d", session_id, bad)
    turn["persist"](answer)
    result = {"answer": answer, "sources": turn["sources"]}
    if answer and answer != GENERATION_FAILED_RESPONSE:
        _ANSWER_CACHE.put(turn["cache_key"], result)
    now = time.perf_counter()
    logger.info(
        "timing | session=%s | retrieval+history=%.2fs | llm=%.2fs | total=%.2fs",
        session_id, turn["t_retrieved"] - turn["t0"], now - turn["t_retrieved"], now - turn["t0"],
    )
    return result


async def run_chat(
    session_id: str,
    chat_input: str,
    user_client: Any,
    user_id: str,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """
    Run one turn of the RAG chat and return the whole answer.

    Retrieval (hybrid_search_rls under the caller's RLS, multi-query for broad
    intents, Cohere rerank), the answer cache, clarifying questions and
    history persistence all live in _prepare_turn/_finish_turn, shared with
    stream_chat. One LLM call generates the answer from the retrieved context
    (no multi-tool-call agent loop, which was prone to re-writing the query
    with junk and giving up).
    """
    llm = make_chat_llm(streaming=False)
    turn = await _prepare_turn(session_id, chat_input, user_client, user_id, tenant_id, llm)
    if "final" in turn:
        return turn["final"]
    try:
        response = await asyncio.to_thread(llm.invoke, turn["messages"])
        answer = getattr(response, "content", "") or ""
    except Exception as exc:  # noqa: BLE001
        # Distinct from the zero-doc guard: retrieval succeeded, the LLM call
        # itself failed (rate limit, timeout, outage). Answering
        # NO_RESULT_RESPONSE here would make an outage look like "no
        # matching documents".
        logger.error("LLM generation failed | session=%s | query=%r | %s", session_id, chat_input, exc)
        answer = GENERATION_FAILED_RESPONSE
    return _finish_turn(session_id, turn, answer)


async def stream_chat(
    session_id: str,
    chat_input: str,
    user_client: Any,
    user_id: str,
    tenant_id: str | None = None,
):
    """
    Same turn as run_chat, streamed. Yields events:
      {"type": "token", "text": ...}  (many; a non-LLM answer arrives as one)
      {"type": "sources", "sources": [...]}  (last)
    The full answer is persisted and cached once streaming completes, even
    if the stream fails part-way (a partial answer is still useful context
    for the next turn).
    """
    llm = make_chat_llm(streaming=True)
    turn = await _prepare_turn(session_id, chat_input, user_client, user_id, tenant_id, llm)
    if "final" in turn:
        yield {"type": "token", "text": turn["final"]["answer"]}
        yield {"type": "sources", "sources": turn["final"]["sources"]}
        return

    parts: list[str] = []
    try:
        async for chunk in llm.astream(turn["messages"]):
            text = str(getattr(chunk, "content", chunk) or "")
            if text:
                parts.append(text)
                yield {"type": "token", "text": text}
    except Exception as exc:  # noqa: BLE001
        logger.error("streaming LLM generation failed | session=%s | query=%r | %s", session_id, chat_input, exc)
        if not parts:
            parts.append(GENERATION_FAILED_RESPONSE)
            yield {"type": "token", "text": GENERATION_FAILED_RESPONSE}
    result = _finish_turn(session_id, turn, "".join(parts))
    yield {"type": "sources", "sources": result["sources"]}

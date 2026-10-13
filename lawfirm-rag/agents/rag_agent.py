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
3. Cite sources with [Title | Matter ID] when referencing retrieved content.
4. If the context does not answer the question, say: "I could not find an answer in the firm's document repository." Then ask ONE short follow-up question that would let you find it (e.g. the case name or parties, the matter ID, or the document type). If the context mentions documents that might be relevant, name them so the user can pick.
5. If the question is ambiguous — it fits more than one document in the context (e.g. "the succession case" when there are several), or it refers to "the judge"/"the contract" with no earlier turn saying which — do not guess. Briefly list the candidates from the context and ask which one the user means.
6. If you can answer only part of the question, answer that part, then say what is missing and ask a follow-up question about it.
7. Never speculate beyond the retrieved content.

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
        if time.monotonic() < self.skip_until:
            return list(documents)[: self.top_n]
        try:
            return self.inner.compress_documents(documents, query, callbacks=callbacks)
        except Exception as exc:  # noqa: BLE001
            self.skip_until = time.monotonic() + 120
            logger.warning("reranker failed, using unreranked RRF order for 120s | %s", exc)
            return list(documents)[: self.top_n]


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


def _build_context_string(docs: list[Any]) -> str:
    """Build a formatted context string from retrieved documents to inject into the prompt."""
    if not docs:
        return "No relevant documents found."
    parts = []
    for i, doc in enumerate(docs, 1):
        meta = getattr(doc, "metadata", {}) or {}
        title = meta.get("file_title") or meta.get("title") or "Unknown document"
        matter_id = meta.get("matter_id", "")
        section = meta.get("section_heading", "")
        header = f"[{i}] {title}"
        if matter_id:
            header += f" | Matter: {matter_id}"
        if section:
            header += f" | {section}"
        parts.append(f"{header}\n{doc.page_content}")
    return "\n\n---\n\n".join(parts)


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
    query_words = set(re.findall(r"[a-z0-9]+", query.lower()))
    for title in candidates:
        title_words = set(re.findall(r"[a-z0-9]+", title.lower())) - _GENERIC_TITLE_WORDS
        if {w for w in title_words if len(w) >= 3} & query_words:
            return None  # the question names one of the documents
    listed = "\n".join(f"- {t}" for t in list(candidates)[:5])
    return f"Your question could refer to more than one document:\n{listed}\n\nWhich one do you mean?"


def _sources_from_docs(docs: list[Any]) -> list[dict[str, Any]]:
    """Build source dicts with title/url/file_id plus chunk-level citation fields.

    Dedupe by (file_id, url, title) — whichever is available. Previously
    required a non-empty ``url`` to include a doc at all, which silently
    hid every uploaded-file source (UI-upload path stores no url, only
    Drive-ingested docs have one) even when the answer cited it.
    """
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for doc in docs:
        meta = getattr(doc, "metadata", {}) or {}
        url = meta.get("url") or meta.get("file_url", "")
        title = meta.get("file_title") or meta.get("title") or "Unknown document"
        file_id = meta.get("file_id", "")
        key = file_id or url or title
        if not key or key in seen:
            continue
        seen.add(key)
        sources.append({
            "title": title,
            "url": url,
            "file_id": file_id,
            "section_heading": meta.get("section_heading", "") or "",
            "chunk_index": meta.get("chunk_index", 0) or 0,
            "page_number": meta.get("page_number", 0) or 0,
        })
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


async def run_chat(
    session_id: str,
    chat_input: str,
    user_client: Any,
    user_id: str,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """
    Run one turn of the RAG chat.

    - Classifies query intent to choose the retrieval depth (or short-circuits).
    - Retrieves via hybrid_search_rls (if present) with RLS; falls back to vector
      similarity, then reranks with Cohere when COHERE_API_KEY is set.
    - Injects the retrieved context directly into the system prompt and generates
      the answer with a single LLM call (no multi-tool-call agent loop, which was
      prone to re-writing the query with junk and giving up).
    - Persists the turn to the Supabase chat_memory table through the caller's
      own client (RLS-enforced ownership; ``user_id`` is stamped on each row).
    """
    t0 = time.perf_counter()
    llm = make_chat_llm(streaming=False)

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
        return cached

    intent = classify_intent(chat_input)
    if intent["intent"] == "out_of_scope":
        logger.info("out_of_scope query | session=%s | query=%r", session_id, chat_input)
        return {"answer": OUT_OF_SCOPE_RESPONSE, "sources": []}

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
        # guard below (silent "no answer"), so this must be visible, not
        # debug-level (which nothing captured before configure_logging()).
        logger.warning("retrieval failed | session=%s | query=%r | %s", session_id, retrieval_query, exc, exc_info=True)

    # Hallucination guard: never call the LLM when nothing was retrieved.
    if not docs:
        logger.info("zero-doc retrieval | session=%s | query=%r", session_id, retrieval_query)
        persist(NO_RESULT_RESPONSE)
        return {"answer": NO_RESULT_RESPONSE, "sources": []}

    sources = _sources_from_docs(docs)[:5]

    # First turn that says "the judge"/"the contract" without naming which,
    # while the hits span several documents: ask instead of guessing. Later
    # turns have history to disambiguate, and broad intents (comparisons,
    # summaries) legitimately span documents.
    if not chat_history and intent["intent"] in ("factual", "procedural"):
        clarify = _clarifying_question(chat_input, docs)
        if clarify:
            persist(clarify)
            logger.info("timing | session=%s | clarifying question | total=%.2fs", session_id, time.perf_counter() - t0)
            return {"answer": clarify, "sources": sources}

    # Build context from retrieved docs and inject into the system message.
    context = _build_context_string(docs)
    system_text = RAG_STREAM_TEMPLATE.format(
        context=context,
    )

    messages: list[Any] = [SystemMessage(content=system_text)]
    messages.extend(chat_history)
    messages.append(HumanMessage(content=chat_input))

    t_retrieved = time.perf_counter()
    answer = ""
    try:
        response = await asyncio.to_thread(llm.invoke, messages)
        answer = getattr(response, "content", "") or ""
    except Exception as exc:  # noqa: BLE001
        # Distinct from the zero-doc guard above: retrieval succeeded (docs
        # is non-empty here), the LLM call itself failed — e.g. a provider
        # rate limit (confirmed: Groq's on_demand-tier TPM cap rejecting
        # requests once retrieved context pushes the prompt over its
        # per-minute token budget). Answering NO_RESULT_RESPONSE here would
        # make a rate-limited/outage request indistinguishable from a query
        # with genuinely no matching documents.
        logger.error(
            "LLM generation failed | session=%s | query=%r | docs_retrieved=%d | %s",
            session_id,
            chat_input,
            len(docs),
            exc,
        )
        answer = GENERATION_FAILED_RESPONSE

    persist(answer)
    result = {"answer": answer, "sources": sources}
    if answer and answer != GENERATION_FAILED_RESPONSE:
        _ANSWER_CACHE.put(cache_key, result)

    logger.info(
        "timing | session=%s | retrieval+history=%.2fs | llm=%.2fs | total=%.2fs",
        session_id, t_retrieved - t0, time.perf_counter() - t_retrieved, time.perf_counter() - t0,
    )
    return {"answer": answer, "sources": sources}


async def stream_chat(
    session_id: str,
    chat_input: str,
    user_client: Any,
    user_id: str,
    tenant_id: str | None = None,
):
    """
    Async generator that yields tokens from the LLM as they arrive.

    - Classifies query intent to choose retrieval depth (or short-circuits).
    - Retrieved docs are built into a context string and injected into the
      system message before streaming (no ungrounded answers).
    - Short-circuits on zero-doc retrieval with the canned no-result response.
    - Persists the full turn to the same chat_memory table run_chat() uses
      once streaming completes, through the caller's own client (RLS-enforced
      ownership); a memory write failure never breaks the stream.
    """
    llm = make_chat_llm(streaming=True)

    intent = classify_intent(chat_input)
    if intent["intent"] == "out_of_scope":
        logger.info("out_of_scope query | session=%s | query=%r", session_id, chat_input)
        yield OUT_OF_SCOPE_RESPONSE
        yield f"data: {json.dumps({'type': 'sources', 'sources': []})}\n\n"
        return

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
    sources: list[dict[str, Any]] = []

    try:
        # ainvoke: MultiQuery runs its rewritten queries concurrently.
        docs = await retriever.ainvoke(chat_input)
        if isinstance(docs, list):
            _log_retrieval(
                session_id=session_id,
                query=chat_input,
                docs=docs,
                reranked=bool(settings.cohere_api_key),
                intent=intent,
                path=hybrid_retriever.last_path,
            )
            sources = _sources_from_docs(docs)[:5]
    except Exception as exc:
        logger.warning("streaming retrieval failed | session=%s | query=%r | %s", session_id, chat_input, exc, exc_info=True)

    # Hallucination guard: never call the LLM when nothing was retrieved.
    if not docs:
        logger.info("zero-doc retrieval | session=%s | query=%r", session_id, chat_input)
        yield NO_RESULT_RESPONSE
        yield f"data: {json.dumps({'type': 'sources', 'sources': []})}\n\n"
        return

    # Build context string from retrieved docs and inject into system message
    context = _build_context_string(docs)
    system_text = RAG_STREAM_TEMPLATE.format(
        context=context,
    )

    sys_msg = SystemMessage(content=system_text)
    human_msg = HumanMessage(content=chat_input)

    tokens_buffer: list[str] = []
    stream_failed = False
    try:
        async for token in llm.astream([sys_msg, human_msg]):
            token_content = getattr(token, "content", token)
            tokens_buffer.append(str(token_content))
            yield token_content
    except Exception as exc:
        # Same distinction as run_chat(): docs were retrieved, the LLM call
        # itself failed (rate limit, timeout, provider outage). Was logged at
        # debug level (never reached any log output — nothing in this
        # codebase called logging.basicConfig()/dictConfig() at the time) and
        # silently dropped the client with no explanation. If no tokens made
        # it out before the failure, surface a distinct message instead of
        # leaving the client with an empty/truncated answer that looks
        # identical to a zero-doc "no result" case.
        logger.error(
            "streaming LLM generation failed | session=%s | query=%r | docs_retrieved=%d | %s",
            session_id,
            chat_input,
            len(docs),
            exc,
        )
        stream_failed = True
        if not tokens_buffer:
            yield GENERATION_FAILED_RESPONSE
            tokens_buffer.append(GENERATION_FAILED_RESPONSE)

    # Persist this turn to chat_memory (same table + format as run_chat()),
    # even when the stream failed partway through — a partial answer is still
    # useful conversational context for the next turn, and the alternative
    # (dropping the turn entirely) silently breaks multi-turn continuity.
    # A persistence failure must never break the stream or drop the sources
    # event, so it is isolated in its own try/except.
    try:
        assembled_response = "".join(tokens_buffer)
        if assembled_response:
            history = SupabaseChatHistory(
                session_id=session_id,
                client=user_client,
                user_id=user_id,
                tenant_id=tenant_id,
            )
            history.add_user_message(chat_input)
            history.add_ai_message(assembled_response)
    except Exception as exc:  # noqa: BLE001
        logger.debug("streaming memory persistence failed: %s", exc)

    # Send the sources event even on a failed generation: retrieval genuinely
    # found these documents (that's the whole point of GENERATION_FAILED_RESPONSE
    # vs. NO_RESULT_RESPONSE above), so the client should still see them rather
    # than silently losing the "sources retrieved" panel on a rate-limit/outage.
    yield f"data: {json.dumps({'type': 'sources', 'sources': sources})}\n\n"
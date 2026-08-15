from __future__ import annotations

import logging
import asyncio
import json
import re
from typing import Any, List

from langchain_cohere import CohereRerank
from langchain_community.vectorstores import SupabaseVectorStore
from langchain_core.retrievers import BaseRetriever
from langchain_core.documents import Document
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_classic.retrievers.multi_query import MultiQueryRetriever

from search.hybrid import hybrid_search
from sessions.supabase_history import SupabaseChatHistory
from config import settings
from providers import make_chat_llm, get_embeddings

logger = logging.getLogger(__name__)

RAG_STREAM_TEMPLATE = """{system_prefix}

You are a secure legal knowledge assistant for Anjarwalla & Khanna Advocates.

CORE BEHAVIOUR RULES:
1. Answer ONLY from the retrieved context below — never from general knowledge.
2. For greetings or small talk: respond briefly without referencing the knowledge base.
3. Cite sources with [Title | Matter ID] when referencing retrieved content.
4. If the context is insufficient say: "I could not find an answer in the firm's document repository."
5. Never speculate beyond the retrieved content.

RETRIEVED CONTEXT:
{context}
"""

NO_RESULT_RESPONSE = "I could not find an answer in the firm's document repository."
OUT_OF_SCOPE_RESPONSE = "This question is outside the scope of the firm's document repository."

# Query-intent → retrieval depth. shorter depths for point questions,
# deeper recalls for summaries/comparisons across the corpus.
INTENT_RETRIEVE_K = {
    "factual": 8,
    "comparative": 15,
    "summarization": 20,
    "procedural": 10,
    "out_of_scope": 0,
}

INTENT_CLASSIFIER_PROMPT = (
    "Classify this legal query into exactly one category. "
    'Reply with JSON only, no other text: {{"intent": "<category>", "retrieve_k": <int>}}. '
    "Query: {query}"
)

# Keyword hints → doc_type. Matched case-insensitively; first hit wins. No LLM call.
DOC_TYPE_HINT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "judgment": ("judgment", "ruling", "decision", "case", "eklr"),
    "contract": ("contract", "agreement", "clause", "nda", "lease"),
    "statute": ("act", "statute", "regulation", "section", "cap."),
}


def _doc_type_hint_from_query(query: str) -> str | None:
    """
    Cheap, LLM-free doc_type hint for a query.

    Returns "judgment", "contract", "statute" or None based on simple keyword
    matching. Used to restrict retrieval to a single document type.
    """
    lowered = query.lower()
    for doc_type, keywords in DOC_TYPE_HINT_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            return doc_type
    return None


def classify_intent(query: str, llm: Any) -> dict[str, Any]:
    """
    Classify a legal query into one of five intents and pick a retrieval depth.

    Uses one cheap LLM call that is instructed to reply with JSON only. The
    response is parsed defensively — any parse failure degrades safely to
    factual/8 so the chat path never breaks on garbage output.

    Also derives a ``doc_type_hint`` from the query via keyword matching (no
    extra LLM call) so retrieval can be scoped to a single document type.
    """
    doc_type_hint = _doc_type_hint_from_query(query)
    try:
        response = llm.invoke(INTENT_CLASSIFIER_PROMPT.format(query=query))
        content = getattr(response, "content", "") or ""
        if not isinstance(content, str):
            content = str(content)

        match = re.search(r"\{.*\}", content, re.DOTALL)
        parsed = json.loads(match.group(0)) if match else json.loads(content)

        intent = parsed.get("intent", "factual")
        if intent not in INTENT_RETRIEVE_K:
            intent = "factual"
        try:
            retrieve_k = int(parsed.get("retrieve_k", INTENT_RETRIEVE_K[intent]))
        except (TypeError, ValueError):
            retrieve_k = INTENT_RETRIEVE_K[intent]
        if retrieve_k <= 0 or retrieve_k > 50:
            retrieve_k = INTENT_RETRIEVE_K[intent]
        return {"intent": intent, "retrieve_k": retrieve_k, "doc_type_hint": doc_type_hint}
    except Exception as exc:  # noqa: BLE001
        logger.debug("intent classification failed, defaulting to factual/8: %s", exc)
        return {"intent": "factual", "retrieve_k": 8, "doc_type_hint": doc_type_hint}


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

    class Config:
        arbitrary_types_allowed = True

    def _get_relevant_documents(self, query: str) -> List[Document]:
        top_k = self.k if self.k is not None else settings.retrieve_top_k
        try:
            docs = hybrid_search(
                self.user_client,
                query,
                match_count=top_k,
                doc_type_filter=self.doc_type_filter,
            )
        except Exception:
            docs = []
        if docs:
            return docs
        # Fallback to vector similarity. When a doc_type filter is set, pass it
        # as a metadata filter so only that document type is matched.
        filter_kwargs = {"filter": {"doc_type": self.doc_type_filter}} if self.doc_type_filter else {}
        return self.store.similarity_search(query, k=top_k, **filter_kwargs)


def _make_retriever(user_client: Any, llm: Any, k: int | None = None, doc_type_filter: str | None = None) -> Any:
    """Build the retrieval chain; ``k`` overrides settings.retrieve_top_k for this call.

    ``doc_type_filter`` (optional) restricts retrieval to a single document type
    (e.g. "judgment", "contract", "statute"). Passing None preserves the existing
    unfiltered behaviour.
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

    # Wrap with multi-query rewriting to broaden retrievals
    multi_retriever = MultiQueryRetriever.from_llm(
        retriever=hybrid_retriever,
        llm=llm,
        include_original=True,
    )

    # Optionally apply Cohere reranking as a contextual compressor
    if settings.cohere_api_key:
        compressor = CohereRerank(
            cohere_api_key=settings.cohere_api_key,
            top_n=5,
            model="rerank-english-v3.0",
        )
        from langchain_classic.retrievers import ContextualCompressionRetriever
        return ContextualCompressionRetriever(
            base_compressor=compressor,
            base_retriever=multi_retriever,
        )

    return multi_retriever


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


def _sources_from_docs(docs: list[Any]) -> list[dict[str, Any]]:
    """Build source dicts with title/url/file_id plus chunk-level citation fields."""
    sources: list[dict[str, Any]] = []
    for doc in docs:
        meta = getattr(doc, "metadata", {}) or {}
        url = meta.get("url") or meta.get("file_url", "")
        title = meta.get("file_title") or meta.get("title") or "Unknown document"
        file_id = meta.get("file_id", "")
        if url and not any(source["url"] == url for source in sources):
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
) -> None:
    """Log retrieval quality info for each query."""
    top_chunks = []
    for doc in docs[:5]:
        meta = getattr(doc, "metadata", {}) or {}
        top_chunks.append({
            "file_id": meta.get("file_id", ""),
            "chunk_index": meta.get("chunk_index", ""),
            "section": meta.get("section_heading", ""),
        })
    logger.info(
        "retrieval | session=%s | query=%r | docs_retrieved=%d | cohere_reranked=%s | top_chunks=%s",
        session_id,
        query,
        len(docs),
        reranked,
        json.dumps(top_chunks),
    )


async def run_chat(
    session_id: str,
    system_prefix: str,
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
    llm = make_chat_llm(streaming=False)

    intent = classify_intent(chat_input, llm)
    if intent["intent"] == "out_of_scope":
        logger.info("out_of_scope query | session=%s | query=%r", session_id, chat_input)
        return {"answer": OUT_OF_SCOPE_RESPONSE, "sources": []}

    retriever = _make_retriever(
        user_client, llm, k=intent["retrieve_k"], doc_type_filter=intent.get("doc_type_hint")
    )
    docs: list[Any] = []
    try:
        docs = await asyncio.to_thread(retriever.invoke, chat_input)
        if isinstance(docs, list):
            _log_retrieval(
                session_id=session_id,
                query=chat_input,
                docs=docs,
                reranked=bool(settings.cohere_api_key),
            )
    except Exception as exc:
        logger.debug("retrieval failed: %s", exc)

    # Hallucination guard: never call the LLM when nothing was retrieved.
    if not docs:
        logger.info("zero-doc retrieval | session=%s | query=%r", session_id, chat_input)
        return {"answer": NO_RESULT_RESPONSE, "sources": []}

    sources = _sources_from_docs(docs)[:5]

    # Build context from retrieved docs and inject into the system message.
    context = _build_context_string(docs)
    system_text = RAG_STREAM_TEMPLATE.format(
        system_prefix=system_prefix,
        context=context,
    )

    # Load recent history (Supabase-backed) so multi-turn context is preserved.
    # Reads run through the caller's own client; RLS returns only their rows.
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

    messages: list[Any] = [SystemMessage(content=system_text)]
    messages.extend(chat_history)
    messages.append(HumanMessage(content=chat_input))

    answer = ""
    try:
        response = llm.invoke(messages)
        answer = getattr(response, "content", "") or ""
    except Exception as exc:  # noqa: BLE001
        logger.error("LLM generation failed for session=%s: %s", session_id, exc)
        answer = NO_RESULT_RESPONSE

    # Persist this turn (user + AI) to chat_memory. A failure must not break the
    # response, so it is isolated in its own try/except.
    try:
        history.add_user_message(chat_input)
        history.add_ai_message(answer)
    except Exception as exc:  # noqa: BLE001
        logger.debug("memory persistence failed: %s", exc)

    return {"answer": answer, "sources": sources}


async def stream_chat(
    session_id: str,
    system_prefix: str,
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

    intent = classify_intent(chat_input, llm)
    if intent["intent"] == "out_of_scope":
        logger.info("out_of_scope query | session=%s | query=%r", session_id, chat_input)
        yield OUT_OF_SCOPE_RESPONSE
        yield f"data: {json.dumps({'type': 'sources', 'sources': []})}\n\n"
        return

    retriever = _make_retriever(
        user_client, llm, k=intent["retrieve_k"], doc_type_filter=intent.get("doc_type_hint")
    )
    docs: list[Any] = []
    sources: list[dict[str, Any]] = []

    try:
        docs = await asyncio.to_thread(retriever.invoke, chat_input)
        if isinstance(docs, list):
            _log_retrieval(
                session_id=session_id,
                query=chat_input,
                docs=docs,
                reranked=bool(settings.cohere_api_key),
            )
            sources = _sources_from_docs(docs)[:5]
    except Exception as exc:
        logger.debug("streaming retrieval failed: %s", exc)

    # Hallucination guard: never call the LLM when nothing was retrieved.
    if not docs:
        logger.info("zero-doc retrieval | session=%s | query=%r", session_id, chat_input)
        yield NO_RESULT_RESPONSE
        yield f"data: {json.dumps({'type': 'sources', 'sources': []})}\n\n"
        return

    # Build context string from retrieved docs and inject into system message
    context = _build_context_string(docs)
    system_text = RAG_STREAM_TEMPLATE.format(
        system_prefix=system_prefix,
        context=context,
    )

    sys_msg = SystemMessage(content=system_text)
    human_msg = HumanMessage(content=chat_input)

    tokens_buffer: list[str] = []
    try:
        async for token in llm.astream([sys_msg, human_msg]):
            token_content = getattr(token, "content", token)
            tokens_buffer.append(str(token_content))
            yield token_content
    except Exception as exc:
        logger.debug("streaming LLM failed: %s", exc)
        return

    # Persist this turn to chat_memory (same table + format as run_chat()).
    # A failure here must never break the stream or drop the sources event,
    # so it is isolated in its own try/except.
    try:
        assembled_response = "".join(tokens_buffer)
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

    yield f"data: {json.dumps({'type': 'sources', 'sources': sources})}\n\n"
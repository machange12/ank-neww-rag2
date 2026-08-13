from __future__ import annotations

import logging
import asyncio
import json
<<<<<<< HEAD
import re
from typing import Any, List

from langchain_classic.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools.retriever import create_retriever_tool
from langchain_cohere import CohereRerank
from langchain_classic.memory import ConversationBufferWindowMemory
from langchain_community.vectorstores import SupabaseVectorStore
from langchain_core.retrievers import BaseRetriever
from langchain_core.documents import Document
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_classic.retrievers.multi_query import MultiQueryRetriever

from search.hybrid import hybrid_search
from search.supabase_client import make_service_client
from sessions.supabase_history import SupabaseChatHistory
from config import settings
from providers import make_chat_llm, get_embeddings
=======
from typing import Any, List

from langchain.agents import AgentExecutor, create_openai_functions_agent
from langchain.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain.tools.retriever import create_retriever_tool
from langchain_cohere import CohereRerank
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_postgres import PostgresChatMessageHistory
from langchain.memory import ConversationBufferWindowMemory
from langchain_community.vectorstores import SupabaseVectorStore
from langchain.schema import BaseRetriever, Document, SystemMessage, HumanMessage
from langchain.retrievers.multi_query import MultiQueryRetriever

from search.hybrid import hybrid_search
from config import settings
>>>>>>> c7d4b0571d87621b092e195d03135e276042d2fc

logger = logging.getLogger(__name__)

RAG_SYSTEM_TEMPLATE = """{system_prefix}

You are a secure legal knowledge assistant for Anjarwalla & Khanna Advocates.

CORE BEHAVIOUR RULES:
1. ALWAYS call the search tool for any legal question — never answer from general knowledge alone.
2. For greetings or small talk: respond briefly, do NOT reference the knowledge base.
3. For legal questions: search first, cite sources with [Title | Date | Matter ID].
4. If insufficient context: "I could not find an answer in the firm's document repository."
5. Never speculate beyond retrieved content.
"""

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

<<<<<<< HEAD
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


def classify_intent(query: str, llm: Any) -> dict[str, Any]:
    """
    Classify a legal query into one of five intents and pick a retrieval depth.

    Uses one cheap LLM call that is instructed to reply with JSON only. The
    response is parsed defensively — any parse failure degrades safely to
    factual/8 so the chat path never breaks on garbage output.
    """
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
        return {"intent": intent, "retrieve_k": retrieve_k}
    except Exception as exc:  # noqa: BLE001
        logger.debug("intent classification failed, defaulting to factual/8: %s", exc)
        return {"intent": "factual", "retrieve_k": 8}


def _make_vector_store(user_client: Any) -> SupabaseVectorStore:
    embeddings = get_embeddings()
=======

def _make_vector_store(user_client: Any) -> SupabaseVectorStore:
    embeddings = OpenAIEmbeddings(
        model=settings.embedding_model,
        openai_api_key=settings.openai_api_key,
    )
>>>>>>> c7d4b0571d87621b092e195d03135e276042d2fc
    return SupabaseVectorStore(
        client=user_client,
        embedding=embeddings,
        table_name="documents",
        query_name="match_documents_rls",  # security invoker — RLS applies
    )


class HybridRetriever(BaseRetriever):
    """Retriever that tries a hybrid (vector + keyword) RPC first and falls back to vector similarity."""

<<<<<<< HEAD
    user_client: Any
    store: Any
    k: int | None = None

    class Config:
        arbitrary_types_allowed = True

    def _get_relevant_documents(self, query: str) -> List[Document]:
        top_k = self.k if self.k is not None else settings.retrieve_top_k
        try:
            docs = hybrid_search(self.user_client, query, match_count=top_k)
=======
    def __init__(self, user_client: Any, store: SupabaseVectorStore):
        self.user_client = user_client
        self.store = store

    def get_relevant_documents(self, query: str) -> List[Document]:
        try:
            docs = hybrid_search(self.user_client, query, match_count=settings.retrieve_top_k)
>>>>>>> c7d4b0571d87621b092e195d03135e276042d2fc
        except Exception:
            docs = []
        if docs:
            return docs
        # Fallback to vector similarity
<<<<<<< HEAD
        return self.store.similarity_search(query, k=top_k)


def _make_retriever(user_client: Any, llm: Any, k: int | None = None) -> Any:
    """Build the retrieval chain; ``k`` overrides settings.retrieve_top_k for this call."""
    if k is None:
        k = settings.retrieve_top_k
    store = _make_vector_store(user_client)

    # Use the hybrid retriever that prefers the RPC-based fusion search
    hybrid_retriever = HybridRetriever(user_client=user_client, store=store, k=k)
=======
        return self.store.similarity_search(query, k=settings.retrieve_top_k)


def _make_retriever(user_client: Any, llm: Any) -> Any:
    store = _make_vector_store(user_client)

    # Use the hybrid retriever that prefers the RPC-based fusion search
    hybrid_retriever = HybridRetriever(user_client=user_client, store=store)
>>>>>>> c7d4b0571d87621b092e195d03135e276042d2fc

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
<<<<<<< HEAD
        from langchain_classic.retrievers import ContextualCompressionRetriever
=======
        from langchain.retrievers import ContextualCompressionRetriever
>>>>>>> c7d4b0571d87621b092e195d03135e276042d2fc
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


<<<<<<< HEAD
def _sources_from_docs(docs: list[Any]) -> list[dict[str, Any]]:
    """Build source dicts with title/url/file_id plus chunk-level citation fields."""
    sources: list[dict[str, Any]] = []
=======
def _sources_from_docs(docs: list[Any]) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
>>>>>>> c7d4b0571d87621b092e195d03135e276042d2fc
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
<<<<<<< HEAD
                "section_heading": meta.get("section_heading", "") or "",
                "chunk_index": meta.get("chunk_index", 0) or 0,
                "page_number": meta.get("page_number", 0) or 0,
=======
>>>>>>> c7d4b0571d87621b092e195d03135e276042d2fc
            })
    return sources


<<<<<<< HEAD
def _sources_from_intermediate_steps(result: dict[str, Any]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
=======
def _sources_from_intermediate_steps(result: dict[str, Any]) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
>>>>>>> c7d4b0571d87621b092e195d03135e276042d2fc
    for step in result.get("intermediate_steps", []):
        if isinstance(step, tuple) and len(step) == 2:
            docs = step[1] if isinstance(step[1], list) else []
            for source in _sources_from_docs(docs):
                if not any(existing["url"] == source["url"] for existing in sources):
                    sources.append(source)
    return sources[:5]


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
) -> dict[str, Any]:
    """
    Run one turn of the RAG chat agent.
<<<<<<< HEAD
    - Classifies query intent to choose the retrieval depth (or short-circuit).
=======
>>>>>>> c7d4b0571d87621b092e195d03135e276042d2fc
    - Retrieves via hybrid_search_rls (if present) with RLS; falls back to vector similarity.
    - Rewrites query with MultiQueryRetriever.from_llm before retrieval.
    - Reranks with Cohere if COHERE_API_KEY is set.
    - Stores conversation in Postgres chat_memory table.
    """
<<<<<<< HEAD
    llm = make_chat_llm(streaming=False)

    intent = classify_intent(chat_input, llm)
    if intent["intent"] == "out_of_scope":
        logger.info("out_of_scope query | session=%s | query=%r", session_id, chat_input)
        return {"answer": OUT_OF_SCOPE_RESPONSE, "sources": []}

    retriever = _make_retriever(user_client, llm, k=intent["retrieve_k"])
    retrieved_sources: list[dict[str, Any]] = []
    docs: list[Any] = []
=======
    llm = ChatOpenAI(
        model=settings.chat_model,
        temperature=0,
        max_tokens=settings.chat_max_tokens,
        openai_api_key=settings.openai_api_key,
    )

    retriever = _make_retriever(user_client, llm)
    retrieved_sources: list[dict[str, str]] = []
>>>>>>> c7d4b0571d87621b092e195d03135e276042d2fc
    try:
        docs = await asyncio.to_thread(retriever.invoke, chat_input)
        if isinstance(docs, list):
            _log_retrieval(
                session_id=session_id,
                query=chat_input,
                docs=docs,
                reranked=bool(settings.cohere_api_key),
            )
            retrieved_sources = _sources_from_docs(docs)[:5]
    except Exception as exc:
        logger.debug("source pre-retrieval failed: %s", exc)

<<<<<<< HEAD
    # Hallucination guard: never call the LLM when nothing was retrieved.
    if not docs:
        logger.info("zero-doc retrieval | session=%s | query=%r", session_id, chat_input)
        return {"answer": NO_RESULT_RESPONSE, "sources": []}

=======
>>>>>>> c7d4b0571d87621b092e195d03135e276042d2fc
    search_tool = create_retriever_tool(
        retriever,
        name="search_law_firm_documents",
        description=(
            "Search the law firm's secure document knowledge base. "
            "Use this for any legal question, case research, or document lookup. "
            "Input: the user's search query."
        ),
    )

    prompt = ChatPromptTemplate.from_messages([
        ("system", RAG_SYSTEM_TEMPLATE),
        MessagesPlaceholder(variable_name="chat_history"),
        ("human", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad"),
    ])

<<<<<<< HEAD
    # Supabase-backed chat memory (no direct Postgres connection required)
    history = SupabaseChatHistory(
        session_id=session_id,
        client=make_service_client(),
=======
    # Postgres-backed chat memory
    history = PostgresChatMessageHistory(
        connection_string=settings.postgres_dsn,
        session_id=session_id,
        table_name="chat_memory",
>>>>>>> c7d4b0571d87621b092e195d03135e276042d2fc
    )
    memory = ConversationBufferWindowMemory(
        memory_key="chat_history",
        chat_memory=history,
        return_messages=True,
<<<<<<< HEAD
        input_key="input",
        output_key="output",
        k=settings.context_window,
    )

    agent = create_tool_calling_agent(llm=llm, tools=[search_tool], prompt=prompt)
=======
        k=settings.context_window,
    )

    agent = create_openai_functions_agent(llm=llm, tools=[search_tool], prompt=prompt)
>>>>>>> c7d4b0571d87621b092e195d03135e276042d2fc
    executor = AgentExecutor(
        agent=agent,
        tools=[search_tool],
        memory=memory,
        verbose=False,
        handle_parsing_errors=True,
        return_intermediate_steps=True,
    )

    result = await executor.ainvoke({
        "input": chat_input,
        "system_prefix": system_prefix,
    })

    return {
        "answer": result.get("output", ""),
        "sources": _sources_from_intermediate_steps(result) or retrieved_sources,
    }


async def stream_chat(
    session_id: str,
    system_prefix: str,
    chat_input: str,
    user_client: Any,
):
    """
    Async generator that yields tokens from the LLM as they arrive.

<<<<<<< HEAD
    - Classifies query intent to choose retrieval depth (or short-circuits).
    - Retrieved docs are built into a context string and injected into the
      system message before streaming (no ungrounded answers).
    - Short-circuits on zero-doc retrieval with the canned no-result response.
    - Persists the full turn to the same chat_memory table run_chat() uses
      once streaming completes; a memory write failure never breaks the stream.
    """
    llm = make_chat_llm(streaming=True)

    intent = classify_intent(chat_input, llm)
    if intent["intent"] == "out_of_scope":
        logger.info("out_of_scope query | session=%s | query=%r", session_id, chat_input)
        yield OUT_OF_SCOPE_RESPONSE
        yield f"data: {json.dumps({'type': 'sources', 'sources': []})}\n\n"
        return

    retriever = _make_retriever(user_client, llm, k=intent["retrieve_k"])
    docs: list[Any] = []
    sources: list[dict[str, Any]] = []
=======
    FIX: Retrieved docs are now built into a context string and injected
    into the system message before streaming. Previously docs were fetched
    but never added to the prompt, meaning the LLM streamed answers with
    no grounding — pure hallucination.
    """
    llm = ChatOpenAI(
        model=settings.chat_model,
        temperature=0,
        max_tokens=settings.chat_max_tokens,
        openai_api_key=settings.openai_api_key,
        streaming=True,
    )

    retriever = _make_retriever(user_client, llm)
    docs: list[Any] = []
    sources: list[dict[str, str]] = []
>>>>>>> c7d4b0571d87621b092e195d03135e276042d2fc

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

<<<<<<< HEAD
    # Hallucination guard: never call the LLM when nothing was retrieved.
    if not docs:
        logger.info("zero-doc retrieval | session=%s | query=%r", session_id, chat_input)
        yield NO_RESULT_RESPONSE
        yield f"data: {json.dumps({'type': 'sources', 'sources': []})}\n\n"
        return

=======
>>>>>>> c7d4b0571d87621b092e195d03135e276042d2fc
    # Build context string from retrieved docs and inject into system message
    context = _build_context_string(docs)
    system_text = RAG_STREAM_TEMPLATE.format(
        system_prefix=system_prefix,
        context=context,
    )

    sys_msg = SystemMessage(content=system_text)
    human_msg = HumanMessage(content=chat_input)

<<<<<<< HEAD
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
            client=make_service_client(),
        )
        history.add_user_message(chat_input)
        history.add_ai_message(assembled_response)
    except Exception as exc:  # noqa: BLE001
        logger.debug("streaming memory persistence failed: %s", exc)

    yield f"data: {json.dumps({'type': 'sources', 'sources': sources})}\n\n"
=======
    try:
        async for token in llm.astream([sys_msg, human_msg]):
            yield getattr(token, "content", token)
        yield f"data: {json.dumps({'type': 'sources', 'sources': sources})}\n\n"
    except Exception as exc:
        logger.debug("streaming LLM failed: %s", exc)
        return
>>>>>>> c7d4b0571d87621b092e195d03135e276042d2fc

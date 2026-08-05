from __future__ import annotations

import logging
import asyncio
import json
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


def _make_vector_store(user_client: Any) -> SupabaseVectorStore:
    embeddings = OpenAIEmbeddings(
        model=settings.embedding_model,
        openai_api_key=settings.openai_api_key,
    )
    return SupabaseVectorStore(
        client=user_client,
        embedding=embeddings,
        table_name="documents",
        query_name="match_documents_rls",  # security invoker — RLS applies
    )


class HybridRetriever(BaseRetriever):
    """Retriever that tries a hybrid (vector + keyword) RPC first and falls back to vector similarity."""

    def __init__(self, user_client: Any, store: SupabaseVectorStore):
        self.user_client = user_client
        self.store = store

    def get_relevant_documents(self, query: str) -> List[Document]:
        try:
            docs = hybrid_search(self.user_client, query, match_count=settings.retrieve_top_k)
        except Exception:
            docs = []
        if docs:
            return docs
        # Fallback to vector similarity
        return self.store.similarity_search(query, k=settings.retrieve_top_k)


def _make_retriever(user_client: Any, llm: Any) -> Any:
    store = _make_vector_store(user_client)
    base_retriever = store.as_retriever(search_kwargs={"k": settings.retrieve_top_k})

    # Use the hybrid retriever that prefers the RPC-based fusion search
    hybrid_retriever = HybridRetriever(user_client=user_client, store=store)

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
        from langchain.retrievers import ContextualCompressionRetriever
        return ContextualCompressionRetriever(
            base_compressor=compressor,
            base_retriever=multi_retriever,
        )

    return multi_retriever


def _sources_from_docs(docs: list[Any]) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
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
            })
    return sources


def _sources_from_intermediate_steps(result: dict[str, Any]) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
    for step in result.get("intermediate_steps", []):
        if isinstance(step, tuple) and len(step) == 2:
            docs = step[1] if isinstance(step[1], list) else []
            for source in _sources_from_docs(docs):
                if not any(existing["url"] == source["url"] for existing in sources):
                    sources.append(source)
    return sources[:5]


async def run_chat(
    session_id: str,
    system_prefix: str,
    chat_input: str,
    user_client: Any,
) -> dict[str, Any]:
    """
    Run one turn of the RAG chat agent.
    - Retrieves via hybrid_search_rls (if present) with RLS; falls back to vector similarity.
    - Rewrites query with MultiQueryRetriever.from_llm before retrieval.
    - Reranks with Cohere if COHERE_API_KEY is set.
    - Stores conversation in Postgres chat_memory table.
    """
    llm = ChatOpenAI(
        model=settings.chat_model,
        temperature=0,
        max_tokens=settings.chat_max_tokens,
        openai_api_key=settings.openai_api_key,
    )

    retriever = _make_retriever(user_client, llm)
    retrieved_sources: list[dict[str, str]] = []
    try:
        docs = await asyncio.to_thread(retriever.invoke, chat_input)
        if isinstance(docs, list):
            retrieved_sources = _sources_from_docs(docs)[:5]
    except Exception as exc:
        logger.debug("source pre-retrieval failed: %s", exc)

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

    # Postgres-backed chat memory
    history = PostgresChatMessageHistory(
        connection_string=settings.postgres_dsn,
        session_id=session_id,
        table_name="chat_memory",
    )
    memory = ConversationBufferWindowMemory(
        memory_key="chat_history",
        chat_memory=history,
        return_messages=True,
        k=settings.context_window,
    )

    agent = create_openai_functions_agent(llm=llm, tools=[search_tool], prompt=prompt)
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

    This streaming path mirrors the run_chat prompt construction but currently
    streams the LLM output directly. Retrieval and tool-based executions are
    still handled in the standard (non-streaming) path via run_chat.
    """
    llm = ChatOpenAI(
        model=settings.chat_model,
        temperature=0,
        max_tokens=settings.chat_max_tokens,
        openai_api_key=settings.openai_api_key,
        streaming=True,
    )

    retriever = _make_retriever(user_client, llm)
    sources: list[dict[str, str]] = []
    try:
        docs = await asyncio.to_thread(retriever.invoke, chat_input)
        if isinstance(docs, list):
            sources = _sources_from_docs(docs)[:5]
    except Exception as exc:
        logger.debug("streaming retrieval failed: %s", exc)

    system_text = RAG_SYSTEM_TEMPLATE.format(system_prefix=system_prefix)
    sys_msg = SystemMessage(content=system_text)
    human_msg = HumanMessage(content=chat_input)

    try:
        async for token in llm.astream([sys_msg, human_msg]):
            yield getattr(token, "content", token)
        yield f"data: {json.dumps({'type': 'sources', 'sources': sources})}\n\n"
    except Exception as exc:
        logger.debug("streaming LLM failed: %s", exc)
        return

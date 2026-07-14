from __future__ import annotations

import logging
from typing import Any

from langchain.agents import AgentExecutor, create_openai_functions_agent
from langchain.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain.tools.retriever import create_retriever_tool
from langchain_cohere import CohereRerank
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_postgres import PostgresChatMessageHistory
from langchain.memory import ConversationBufferWindowMemory
from langchain_community.vectorstores import SupabaseVectorStore

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


def _make_retriever(user_client: Any) -> Any:
    store = _make_vector_store(user_client)
    base_retriever = store.as_retriever(search_kwargs={"k": settings.retrieve_top_k})

    if settings.cohere_api_key:
        compressor = CohereRerank(
            cohere_api_key=settings.cohere_api_key,
            top_n=5,
            model="rerank-english-v3.0",
        )
        from langchain.retrievers import ContextualCompressionRetriever
        return ContextualCompressionRetriever(
            base_compressor=compressor,
            base_retriever=base_retriever,
        )

    return base_retriever


def run_chat(
    session_id: str,
    system_prefix: str,
    chat_input: str,
    user_client: Any,
) -> str:
    """
    Run one turn of the RAG chat agent.
    - Retrieves via match_documents_rls (security invoker, RLS-gated).
    - Reranks with Cohere if COHERE_API_KEY is set.
    - Stores conversation in Postgres chat_memory table.
    """
    llm = ChatOpenAI(
        model=settings.chat_model,
        temperature=0,
        max_tokens=settings.chat_max_tokens,
        openai_api_key=settings.openai_api_key,
    )

    retriever = _make_retriever(user_client)
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
    )

    result = executor.invoke({
        "input": chat_input,
        "system_prefix": system_prefix,
    })

    return result.get("output") or ""

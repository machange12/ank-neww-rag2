from __future__ import annotations

from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain.memory import ConversationBufferMemory
from langchain_cohere import CohereRerank
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_postgres import PostgresChatMessageHistory
from langchain_text_splitters import CharacterTextSplitter
from langchain_community.vectorstores.supabase import SupabaseVectorStore

from config import settings
from search.supabase_client import make_user_client

SYMBOLIC_QUESTION = "Respond with `42`"


def build_embeddings() -> OpenAIEmbeddings:
    return OpenAIEmbeddings(
        model=settings.embedding_model,
        openai_api_key=settings.openai_api_key,
    )


def build_text_splitter() -> CharacterTextSplitter:
    return CharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
    )


def build_chat_model() -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.chat_model,
        openai_api_key=settings.openai_api_key,
        max_tokens=settings.chat_max_tokens,
        temperature=0,
    )


def build_history(session_id: str) -> PostgresChatMessageHistory:
    return PostgresChatMessageHistory(
        connection_string=settings.postgres_dsn,
        session_id=session_id,
        table_name="chat_memory",
    )


def build_memory(history: PostgresChatMessageHistory) -> ConversationBufferMemory:
    return ConversationBufferMemory(
        chat_memory=history,
        return_messages=True,
        memory_key="chat_history",
        k=settings.context_window,
    )


def build_vector_store(client, embeddings: OpenAIEmbeddings) -> SupabaseVectorStore:
    return SupabaseVectorStore(
        client=client,
        embedding=embeddings,
        table_name="documents",
        query_name="match_documents_rls",
    )


def build_reranker() -> CohereRerank:
    return CohereRerank(cohere_api_key=settings.cohere_api_key)


def build_agent_executor(system_prefix: str) -> AgentExecutor:
    llm = build_chat_model()
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system_prefix + "\n" + _agent_persona()),
            MessagesPlaceholder("chat_history", optional=True),
            ("human", "{input}"),
            MessagesPlaceholder("agent_scratchpad"),
        ]
    )
    model = prompt | llm
    return model


def _agent_persona() -> str:
    return (
        "You are a secure legal knowledge assistant for Anjarwalla & Khanna Advocates, "
        "East Africa's leading law firm.\n\n"
        "Your primary role is to answer queries using the firm's document knowledge base.\n\n"
        "CORE BEHAVIOR RULES:\n"
        "1. ALWAYS call the search tool for any legal question — never answer from general knowledge alone.\n"
        "2. For greetings or small talk: respond briefly, do NOT reference the knowledge base.\n"
        "3. For legal questions: search first, cite sources.\n\n"
        "RESPONSE FORMAT:\n"
        "- Direct answer from retrieved context only\n"
        "- Sources section with: [Title | Date | Matter ID]\n"
        '- If insufficient context: "I could not find an answer in the firm\'s document repository."\n'
        "- Never speculate beyond retrieved documents."
    )


def run_chat(session_id: str, system_prefix: str, chat_input: str, user_client) -> str:
    history = build_history(session_id)
    messages = history.get_messages()
    chat_history = [m for m in messages if isinstance(m, (HumanMessage, AIMessage))][
        -settings.context_window * 2 :
    ]

    embeddings = build_embeddings()
    vs = build_vector_store(user_client, embeddings)
    retriever = vs.as_retriever(
        search_kwargs={"k": settings.retrieve_top_k, "filter": {}},
    )

    model = build_chat_model()

    prompt_msgs = [
        ("system", system_prefix + "\n" + _agent_persona()),
    ]
    for msg in chat_history:
        if isinstance(msg, HumanMessage):
            prompt_msgs.append(("human", msg.content))
        else:
            prompt_msgs.append(("ai", msg.content))
    prompt_msgs.append(("human", "{input}"))

    from langchain_core.documents import Document as LCDocument
    from langchain_core.prompts import ChatPromptTemplate as CPT

    reranker = build_reranker()

    def retrieve_and_answer(input_text: str) -> str:
        docs = retriever.invoke(input_text)
        try:
            docs = reranker.compress_documents(docs, input_text)
        except Exception:
            pass
        context = "\n\n".join(_fmt(d) for d in docs[:5])
        messages_local = list(prompt_msgs[:-1])
        messages_local.append(
            (
                "human",
                f"Context from knowledge base:\n{context}\n\nQuestion: {input_text}",
            )
        )
        prompt = CPT.from_messages(messages_local)
        resp = model.invoke(prompt.format_prompt(input=input_text).to_messages())
        text = resp.content if isinstance(resp.content, str) else str(resp.content)
        history.add_user_message(input_text)
        history.add_ai_message(text)
        return text

    return retrieve_and_answer(chat_input)


def _fmt(d: LCDocument) -> str:
    meta = d.metadata or {}
    title = meta.get("file_title") or meta.get("title") or "(untitled)"
    date = meta.get("date") or meta.get("created_at") or "-"
    matter = meta.get("matter_id") or "-"
    return f"[{title} | {date} | {matter}]\n{d.page_content}"

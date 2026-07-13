from __future__ import annotations

from langchain_community.vectorstores.supabase import SupabaseVectorStore
from langchain_core.documents import Document


def insert_documents(client, embeddings, docs: list[Document], file_id: str, file_title: str, file_url: str) -> None:
    store = SupabaseVectorStore(
        client=client,
        embedding=embeddings,
        table_name="documents",
        query_name="match_documents",
    )
    vectors = embeddings.embed_documents([d.page_content for d in docs])
    payloads = [
        {
            "content": d.page_content,
            "metadata": {
                "file_id": file_id,
                "file_title": file_title,
                "url": file_url,
                **(d.metadata or {}),
            },
        }
        for d in docs
    ]
    if vectors and payloads and len(vectors[0]) > 0:
        store.add_embeddings(zip(vectors, payloads))
    else:
        store.add_documents(docs)


def build_documents(text: str, metadata: dict) -> list[Document]:
    from langchain_text_splitters import CharacterTextSplitter
    from config import settings

    splitter = CharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
    )
    chunks = splitter.split_text(text or "")
    return [Document(page_content=c, metadata=metadata) for c in chunks]


def delete_old_rows_for_file(client, file_id: str) -> None:
    try:
        client.rpc(
            "delete_documents_by_file_id",
            {"p_file_id": file_id},
        ).execute()
    except Exception:
        try:
            table = client.table("documents")
            table.delete().contains("metadata", {"file_id": file_id}).execute()
        except Exception:
            pass


def insert_metadata(client, payload: dict) -> None:
    client.table("document_metadata").insert(payload).execute()


def delete_orphan_documents(client, ids: list[int]) -> None:
    if not ids:
        return
    try:
        client.table("documents").delete().in_("id", ids).execute()
    except Exception:
        pass


def get_all_documents(client) -> list[dict]:
    res = client.table("documents").select("*").execute()
    return list(getattr(res, "data", []) or [])


def list_metadata(client) -> list[dict]:
    res = client.table("document_metadata").select("*").execute()
    return list(getattr(res, "data", []) or [])


def delete_metadata(client, ids: list[str]) -> None:
    if not ids:
        return
    try:
        client.table("document_metadata").delete().in_("id", ids).execute()
    except Exception:
        pass

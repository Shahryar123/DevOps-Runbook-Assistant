"""Embeddings, the Chroma vector store, and the retriever built on top of it."""

from langchain_chroma import Chroma
from langchain_core.vectorstores import VectorStoreRetriever
from langchain_openai import OpenAIEmbeddings

from runbook_assistant.config import get_settings


def get_embeddings() -> OpenAIEmbeddings:
    """Embedding model shared by ingest and retrieval.

    Both sides must use the same model, otherwise query vectors and stored
    vectors live in different "spaces" and similarity search is meaningless.
    """
    settings = get_settings()
    return OpenAIEmbeddings(
        model=settings.embedding_model, api_key=settings.openai_api_key
    )


def get_vector_store() -> Chroma:
    """Open (or create) the persistent Chroma collection.

    A vector store saves (text, metadata, vector) triples and answers
    "which stored texts are closest to this vector?".
    """
    settings = get_settings()
    return Chroma(
        collection_name=settings.chroma_collection,
        embedding_function=get_embeddings(),
        persist_directory=str(settings.chroma_dir),
    )


def get_retriever(k: int | None = None) -> VectorStoreRetriever:
    """Return a retriever that fetches the top-k most similar chunks.

    A retriever is a Runnable: .invoke("question") -> list[Document].
    It hides how documents are found, so it can be swapped later
    (e.g. hybrid search) without changing the chain.
    """
    top_k = k if k is not None else get_settings().retriever_k
    return get_vector_store().as_retriever(search_kwargs={"k": top_k})

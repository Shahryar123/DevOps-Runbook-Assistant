"""Embeddings, the Chroma vector store, and the retriever built on top of it."""

import threading
from typing import NamedTuple

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


_vector_store: Chroma | None = None
_vector_store_lock = threading.Lock()


def get_vector_store() -> Chroma:
    """Open (or create) the persistent Chroma collection, once per process.

    A vector store saves (text, metadata, vector) triples and answers
    "which stored texts are closest to this vector?".

    Creating a local Chroma client is not thread-safe: evaluations run
    several questions in parallel threads, and concurrent creation crashes
    inside chromadb. So the store is created once, under a lock, and reused.
    Searching a shared store from several threads is fine.
    """
    global _vector_store
    with _vector_store_lock:
        if _vector_store is None:
            settings = get_settings()
            _vector_store = Chroma(
                collection_name=settings.chroma_collection,
                embedding_function=get_embeddings(),
                persist_directory=str(settings.chroma_dir),
            )
        return _vector_store


class RunbookInfo(NamedTuple):
    title: str
    source: str
    topic: str


def list_runbooks() -> list[RunbookInfo]:
    """The runbooks currently in the vector store, one entry per file.

    Reads only chunk metadata (no embeddings, no OpenAI call) and keeps one
    entry per source file. An empty list means nothing has been ingested.
    """
    metadatas = get_vector_store().get(include=["metadatas"])["metadatas"]
    unique = {
        meta["source"]: RunbookInfo(
            title=str(meta.get("title", meta["source"])),
            source=str(meta["source"]),
            topic=str(meta.get("topic", "")),
        )
        for meta in metadatas
        if meta and "source" in meta
    }
    return sorted(unique.values(), key=lambda info: info.title.lower())


def get_retriever(
    k: int | None = None, metadata_filter: dict[str, str] | None = None
) -> VectorStoreRetriever:
    """Return a retriever that fetches the top-k most similar chunks.

    A retriever is a Runnable: .invoke("question") -> list[Document].
    It hides how documents are found, so it can be swapped later
    (e.g. hybrid search) without changing the chain.

    `metadata_filter` is a Chroma filter, e.g. {"topic": "networking"}:
    only chunks whose metadata matches are considered in the search.
    """
    search_kwargs: dict = {"k": k if k is not None else get_settings().retriever_k}
    if metadata_filter:
        search_kwargs["filter"] = metadata_filter
    return get_vector_store().as_retriever(search_kwargs=search_kwargs)

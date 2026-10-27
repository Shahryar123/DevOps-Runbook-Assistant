"""Embeddings, and the retriever built on top of the vector index."""

from typing import NamedTuple

from langchain_core.retrievers import BaseRetriever

from runbook_assistant.config import get_settings
from runbook_assistant.llm import get_embeddings
from runbook_assistant.vectorstores.factory import get_vector_index

__all__ = ["RunbookInfo", "get_embeddings", "get_retriever", "list_runbooks"]


class RunbookInfo(NamedTuple):
    title: str
    source: str
    topic: str


def list_runbooks() -> list[RunbookInfo]:
    """The runbooks currently in the vector index, one entry per file.

    Reads only chunk metadata (no embeddings, no OpenAI call) and keeps one
    entry per source file. An empty list means nothing has been ingested.
    """
    unique = {
        meta["source"]: RunbookInfo(
            title=str(meta.get("title", meta["source"])),
            source=str(meta["source"]),
            topic=str(meta.get("topic", "")),
        )
        for meta in get_vector_index().list_metadata()
        if meta and "source" in meta
    }
    return sorted(unique.values(), key=lambda info: info.title.lower())


def get_retriever(
    k: int | None = None, metadata_filter: dict[str, str] | None = None
) -> BaseRetriever:
    """Return a retriever that fetches the top-k most similar chunks.

    A retriever is a Runnable: .invoke("question") -> list[Document].
    It hides how documents are found, so the store (Chroma or Azure AI
    Search) and the mode (vector or hybrid) can change without touching
    the chain or graph.

    `metadata_filter` is an exact-match filter, e.g. {"topic": "networking"}:
    only chunks whose metadata matches are considered in the search.
    """
    return get_vector_index().as_retriever(
        k=k if k is not None else get_settings().retriever_k,
        metadata_filter=metadata_filter,
    )

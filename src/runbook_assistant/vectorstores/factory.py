"""Pick the vector store from VECTOR_STORE.

- open_index(settings, embeddings): a new VectorIndex (the ingest pipeline
  opens one with its pre-computed embeddings).
- get_vector_index(): the shared VectorIndex the retriever and UI use,
  created once per process under a lock.
- index_exists(settings): has ingest run? Checked without creating anything.

The Azure module is imported only when it's used, so a Chroma setup never
loads the Azure SDK.
"""

import threading

from langchain_core.embeddings import Embeddings

from runbook_assistant.config import Settings, get_settings
from runbook_assistant.vectorstores.base import VectorIndex


def _index_class(settings: Settings) -> type:
    if settings.vector_store == "azure_search":
        from runbook_assistant.vectorstores.azure_search import (
            AzureSearchIndex,
        )

        return AzureSearchIndex
    from runbook_assistant.vectorstores.chroma import ChromaIndex

    return ChromaIndex


def open_index(settings: Settings, embeddings: Embeddings) -> VectorIndex:
    return _index_class(settings)(settings, embeddings)


def index_exists(settings: Settings) -> bool:
    return _index_class(settings).exists(settings)


_shared_index: VectorIndex | None = None
_shared_index_lock = threading.Lock()


def get_vector_index() -> VectorIndex:
    """The shared index, created once, under a lock.

    Creating a local Chroma client is not thread-safe: evaluations run
    several questions in parallel threads, and concurrent creation crashes
    inside chromadb. Searching a shared index from several threads is fine.
    """
    global _shared_index
    with _shared_index_lock:
        if _shared_index is None:
            from runbook_assistant.retriever import get_embeddings

            _shared_index = open_index(get_settings(), get_embeddings())
        return _shared_index

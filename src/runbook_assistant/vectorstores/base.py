"""The interface every vector store implements."""

from collections.abc import Sequence
from typing import Protocol

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.retrievers import BaseRetriever

# Chunk metadata stored with every chunk (and, for Azure, as filterable fields).
METADATA_FIELDS = (
    "source",
    "title",
    "topic",
    "section",
    "content_hash",
    "ingested_at",
    "chunk_id",
)


class VectorIndex(Protocol):
    kind: str  # "chroma" or "azure_search"

    def as_retriever(
        self, k: int, metadata_filter: dict[str, str] | None = None
    ) -> BaseRetriever:
        """A retriever returning the top-k chunks, optionally filtered by
        exact-match metadata, e.g. {"topic": "networking"}."""
        ...

    def add_documents(self, docs: Sequence[Document], ids: Sequence[str]) -> None:
        """Insert or replace (upsert) chunks under our chunk IDs ('file.md::0')."""
        ...

    def delete(self, ids: Sequence[str]) -> None:
        """Delete chunks by our chunk IDs. Unknown IDs are ignored."""
        ...

    def count(self) -> int: ...

    def list_metadata(self) -> list[dict]:
        """Metadata of every chunk (no vectors). Used for the runbook list in
        the UI and to rebuild the manifest from the index."""
        ...

    def reset(self) -> None:
        """Remove every chunk (full rebuild)."""
        ...


class NoEmbeddings(Embeddings):
    """For read-only access (UI listing, dry runs, manifest loading): calling
    the embedding model here would be a bug, so it fails loudly."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("this vector index was opened read-only (no embeddings)")

    def embed_query(self, text: str) -> list[float]:
        raise RuntimeError("this vector index was opened read-only (no embeddings)")

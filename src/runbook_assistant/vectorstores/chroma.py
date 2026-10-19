"""Chroma: a local vector store in a folder (chroma_db/). The default."""

from collections.abc import Sequence

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.retrievers import BaseRetriever

from runbook_assistant.config import Settings


class ChromaIndex:
    kind = "chroma"

    def __init__(self, settings: Settings, embeddings: Embeddings) -> None:
        self.store = Chroma(
            collection_name=settings.chroma_collection,
            embedding_function=embeddings,
            persist_directory=str(settings.chroma_dir),
        )

    @staticmethod
    def exists(settings: Settings) -> bool:
        # Checked without opening Chroma, which would create an empty folder.
        return settings.chroma_dir.exists()

    def as_retriever(
        self, k: int, metadata_filter: dict[str, str] | None = None
    ) -> BaseRetriever:
        search_kwargs: dict = {"k": k}
        if metadata_filter:
            search_kwargs["filter"] = metadata_filter  # Chroma: a plain dict
        return self.store.as_retriever(search_kwargs=search_kwargs)

    def add_documents(self, docs: Sequence[Document], ids: Sequence[str]) -> None:
        if docs:
            self.store.add_documents(list(docs), ids=list(ids))  # upsert

    def delete(self, ids: Sequence[str]) -> None:
        if ids:
            self.store.delete(ids=list(ids))

    def count(self) -> int:
        return len(self.store.get(include=[])["ids"])

    def list_metadata(self) -> list[dict]:
        data = self.store.get(include=["metadatas"])
        return [
            {**(meta or {}), "chunk_id": chunk_id}
            for chunk_id, meta in zip(data["ids"], data["metadatas"], strict=True)
        ]

    def reset(self) -> None:
        self.store.reset_collection()

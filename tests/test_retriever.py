"""Retriever and shared vector index. No OpenAI or Chroma calls."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from runbook_assistant import retriever
from runbook_assistant.vectorstores import factory


def test_shared_index_created_once_across_threads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[object] = []
    lock = threading.Lock()

    def slow_open_index(settings: object, embeddings: object) -> object:
        time.sleep(0.05)  # widen the race window
        index = object()
        with lock:
            created.append(index)
        return index

    monkeypatch.setattr(factory, "open_index", slow_open_index)
    monkeypatch.setattr(factory, "get_settings", lambda: None)
    monkeypatch.setattr(retriever, "get_embeddings", lambda: None)
    monkeypatch.setattr(factory, "_shared_index", None)

    with ThreadPoolExecutor(max_workers=8) as pool:
        indexes = list(pool.map(lambda _: factory.get_vector_index(), range(8)))

    assert len(created) == 1
    assert all(index is created[0] for index in indexes)


class FakeIndex:
    def __init__(self, metadatas: list[dict]) -> None:
        self.metadatas = metadatas
        self.retriever_args: tuple | None = None

    def list_metadata(self) -> list[dict]:
        return self.metadatas

    def as_retriever(self, k: int, metadata_filter: dict | None = None) -> str:
        self.retriever_args = (k, metadata_filter)
        return "retriever"


def test_list_runbooks_one_entry_per_file_sorted_by_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunk_b = {"source": "b.md", "title": "Beta", "topic": "cicd"}
    chunk_a = {"source": "a.md", "title": "alpha", "topic": "security"}
    index = FakeIndex([chunk_b, chunk_a, chunk_b, {}])
    monkeypatch.setattr(retriever, "get_vector_index", lambda: index)

    assert retriever.list_runbooks() == [
        retriever.RunbookInfo(title="alpha", source="a.md", topic="security"),
        retriever.RunbookInfo(title="Beta", source="b.md", topic="cicd"),
    ]


def test_get_retriever_passes_k_and_filter_to_the_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = FakeIndex([])
    monkeypatch.setattr(retriever, "get_vector_index", lambda: index)

    result = retriever.get_retriever(k=7, metadata_filter={"topic": "cicd"})
    assert result == "retriever"
    assert index.retriever_args == (7, {"topic": "cicd"})

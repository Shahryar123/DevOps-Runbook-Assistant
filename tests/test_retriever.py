"""Vector store creation must be thread-safe. No OpenAI or Chroma calls."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from runbook_assistant import retriever
from runbook_assistant.config import Settings


def test_vector_store_created_once_across_threads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[object] = []
    lock = threading.Lock()

    class SlowFakeChroma:
        def __init__(self, **_: object) -> None:
            time.sleep(0.05)  # widen the race window
            with lock:
                created.append(self)

    settings = Settings(openai_api_key="sk-test", _env_file=None)
    monkeypatch.setattr(retriever, "Chroma", SlowFakeChroma)
    monkeypatch.setattr(retriever, "get_settings", lambda: settings)
    monkeypatch.setattr(retriever, "get_embeddings", lambda: None)
    monkeypatch.setattr(retriever, "_vector_store", None)

    with ThreadPoolExecutor(max_workers=8) as pool:
        stores = list(pool.map(lambda _: retriever.get_vector_store(), range(8)))

    assert len(created) == 1
    assert all(store is created[0] for store in stores)


def test_list_runbooks_one_entry_per_file_sorted_by_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeStore:
        def get(self, include: list[str]) -> dict:
            assert include == ["metadatas"]  # metadata only, no embeddings
            chunk_b = {"source": "b.md", "title": "Beta", "topic": "cicd"}
            chunk_a = {"source": "a.md", "title": "alpha", "topic": "security"}
            return {"metadatas": [chunk_b, chunk_a, chunk_b, None]}

    monkeypatch.setattr(retriever, "get_vector_store", FakeStore)

    assert retriever.list_runbooks() == [
        retriever.RunbookInfo(title="alpha", source="a.md", topic="security"),
        retriever.RunbookInfo(title="Beta", source="b.md", topic="cicd"),
    ]

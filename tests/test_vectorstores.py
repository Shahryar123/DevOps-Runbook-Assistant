"""Vector store factory, Azure AI Search adapter and index-derived manifest.

No OpenAI or Azure calls: the AzureSearch class, the index client and
DefaultAzureCredential are replaced with fakes that record what they get.
"""

import json
import re
from pathlib import Path
from typing import ClassVar

import pytest
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import ResourceNotFoundError
from langchain_core.documents import Document
from langchain_core.embeddings import DeterministicFakeEmbedding

from runbook_assistant.config import Settings
from runbook_assistant.pipeline.manifest import (
    fingerprint,
    fingerprint_json,
    manifest_from_metadata,
)
from runbook_assistant.pipeline.run import EXIT_OK, PipelineOptions, run_pipeline
from runbook_assistant.pipeline.sources import LocalFolderSource
from runbook_assistant.vectorstores import azure_search, factory
from runbook_assistant.vectorstores.azure_search import (
    AzureSearchIndex,
    decode_key,
    encode_key,
    index_fields,
    make_credential,
    odata_filter,
)
from runbook_assistant.vectorstores.base import NoEmbeddings
from runbook_assistant.vectorstores.chroma import ChromaIndex

ENDPOINT = "https://example.search.windows.net"


def azure_settings(**overrides: object) -> Settings:
    values: dict = {
        "vector_store": "azure_search",
        "azure_search_endpoint": ENDPOINT,
        "azure_search_index_name": "runbooks",
    }
    return Settings(openai_api_key="sk-test", _env_file=None, **(values | overrides))


# --- Fakes for the Azure SDK -------------------------------------------------------


class FakeSearchClient:
    def __init__(self) -> None:
        self.results: list[dict] = []
        self.search_calls: list[dict] = []

    def get_document_count(self) -> int:
        return len(self.results)

    def search(self, **kwargs: object) -> list[dict]:
        self.search_calls.append(kwargs)
        return self.results


class FakeAzureSearch:
    """Stands in for langchain_azure_ai's AzureSearch; records every call."""

    instances: ClassVar[list["FakeAzureSearch"]] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.client = FakeSearchClient()
        self.added: list[dict] = []
        self.deleted: list[list[str]] = []
        self.retriever_kwargs: dict | None = None
        FakeAzureSearch.instances.append(self)

    def add_texts(
        self, texts: list[str], metadatas: list[dict], keys: list[str]
    ) -> None:
        self.added.append({"texts": texts, "metadatas": metadatas, "keys": keys})

    def delete(self, ids: list[str]) -> bool:
        self.deleted.append(ids)
        return True

    def as_retriever(self, **kwargs: object) -> str:
        self.retriever_kwargs = kwargs
        return "azure-retriever"


class FakeCredential:
    """Stands in for DefaultAzureCredential (Managed Identity in Azure)."""

    def get_token(self, *scopes: str, **kwargs: object) -> None:
        raise AssertionError("tests must not request real tokens")


class FakeIndexClient:
    calls: ClassVar[list[tuple]] = []
    existing: ClassVar[set[str]] = set()

    def __init__(self, endpoint: str, credential: object) -> None:
        FakeIndexClient.calls.append(("init", endpoint, type(credential).__name__))

    def get_index(self, name: str) -> object:
        if name not in FakeIndexClient.existing:
            raise ResourceNotFoundError("no such index")
        return object()

    def delete_index(self, name: str) -> None:
        FakeIndexClient.calls.append(("delete_index", name))


@pytest.fixture
def fake_azure(monkeypatch: pytest.MonkeyPatch) -> type[FakeAzureSearch]:
    FakeAzureSearch.instances = []
    FakeIndexClient.calls = []
    FakeIndexClient.existing = set()
    monkeypatch.setattr(azure_search, "AzureSearch", FakeAzureSearch)
    monkeypatch.setattr(azure_search, "DefaultAzureCredential", FakeCredential)
    monkeypatch.setattr(azure_search, "SearchIndexClient", FakeIndexClient)
    return FakeAzureSearch


# --- Factory -------------------------------------------------------------------------


def test_factory_returns_chroma_by_default(tmp_path: Path) -> None:
    settings = Settings(openai_api_key="sk-test", _env_file=None, chroma_dir=tmp_path)
    index = factory.open_index(settings, NoEmbeddings())
    assert isinstance(index, ChromaIndex)
    assert index.kind == "chroma"


def test_factory_returns_azure_search_when_configured(fake_azure: type) -> None:
    index = factory.open_index(azure_settings(), NoEmbeddings())
    assert isinstance(index, AzureSearchIndex)
    assert index.kind == "azure_search"


def test_index_exists_never_creates_anything(tmp_path: Path, fake_azure: type) -> None:
    chroma = Settings(
        openai_api_key="sk-test", _env_file=None, chroma_dir=tmp_path / "x"
    )
    assert factory.index_exists(chroma) is False
    assert not (tmp_path / "x").exists()

    assert factory.index_exists(azure_settings()) is False
    FakeIndexClient.existing.add("runbooks")
    assert factory.index_exists(azure_settings()) is True
    assert fake_azure.instances == []  # AzureSearch (which creates) never opened


# --- Azure: auth --------------------------------------------------------------------------


def test_api_key_is_used_when_set(fake_azure: type) -> None:
    AzureSearchIndex(azure_settings(azure_search_api_key="test-key"), NoEmbeddings())
    kwargs = fake_azure.instances[0].kwargs
    assert kwargs["azure_search_key"] == "test-key"
    assert "azure_credential" not in kwargs
    assert isinstance(
        make_credential(azure_settings(azure_search_api_key="k")), AzureKeyCredential
    )


def test_default_azure_credential_without_key(fake_azure: type) -> None:
    AzureSearchIndex(azure_settings(), NoEmbeddings())
    kwargs = fake_azure.instances[0].kwargs
    assert kwargs["azure_search_key"] is None
    assert isinstance(kwargs["azure_credential"], FakeCredential)
    assert isinstance(make_credential(azure_settings()), FakeCredential)


def test_index_is_opened_with_our_fields_and_no_embedding_call(
    fake_azure: type,
) -> None:
    AzureSearchIndex(azure_settings(embedding_dimensions=1536), NoEmbeddings())
    kwargs = fake_azure.instances[0].kwargs
    assert kwargs["azure_search_endpoint"] == ENDPOINT
    assert kwargs["index_name"] == "runbooks"
    assert kwargs["vector_search_dimensions"] == 1536  # so no embed_query("Text")
    fields = {f.name: f for f in kwargs["fields"]}
    assert {"id", "content", "content_vector", "metadata"} <= set(fields)
    for name in ("source", "title", "topic", "section", "content_hash", "ingested_at"):
        assert fields[name].filterable, name
    assert fields["id"].key


def test_index_fields_vector_size() -> None:
    vector = {f.name: f for f in index_fields(3072)}["content_vector"]
    assert vector.vector_search_dimensions == 3072


# --- Azure: retrieval -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "search_type"), [("vector", "similarity"), ("hybrid", "hybrid")]
)
def test_retrieval_mode_maps_to_search_type(
    fake_azure: type, mode: str, search_type: str
) -> None:
    index = AzureSearchIndex(azure_settings(retrieval_mode=mode), NoEmbeddings())
    assert fake_azure.instances[0].kwargs["search_type"] == search_type

    assert (
        index.as_retriever(k=4, metadata_filter={"topic": "networking"})
        == "azure-retriever"
    )
    assert fake_azure.instances[0].retriever_kwargs == {
        "search_type": search_type,
        "k": 4,
        "search_kwargs": {"filters": "topic eq 'networking'"},
    }


def test_retriever_without_filter(fake_azure: type) -> None:
    AzureSearchIndex(azure_settings(), NoEmbeddings()).as_retriever(k=2)
    assert fake_azure.instances[0].retriever_kwargs["search_kwargs"] == {}


def test_odata_filter_building_and_escaping() -> None:
    assert odata_filter(None) is None
    assert odata_filter({}) is None
    assert odata_filter({"topic": "networking"}) == "topic eq 'networking'"
    assert odata_filter({"topic": "it's"}) == "topic eq 'it''s'"
    assert odata_filter({"topic": "cicd", "source": "a.md"}) == (
        "topic eq 'cicd' and source eq 'a.md'"
    )
    with pytest.raises(ValueError, match="invalid filter field"):
        odata_filter({"topic eq 'x' or 1 eq 1 or topic": "y"})


# --- Azure: writes and reads ------------------------------------------------------------------

AZURE_KEY = re.compile(r"^[A-Za-z0-9_\-=]+$")


def test_chunk_ids_become_valid_azure_keys() -> None:
    for chunk_id in ("iis-app-pool-crashes.md::0", "a b/c.md::12", "ünïcode.md::3"):
        key = encode_key(chunk_id)
        assert AZURE_KEY.match(key), key
        assert decode_key(key) == chunk_id


def test_add_and_delete_use_encoded_keys(fake_azure: type) -> None:
    index = AzureSearchIndex(azure_settings(), NoEmbeddings())
    docs = [Document(page_content="text", metadata={"source": "a.md", "topic": "cicd"})]

    index.add_documents(docs, ["a.md::0"])
    index.delete(["a.md::0"])
    index.delete([])  # nothing to do: no call
    index.add_documents([], [])

    store = fake_azure.instances[0]
    assert store.added == [
        {
            "texts": ["text"],
            "metadatas": [{"source": "a.md", "topic": "cicd", "chunk_id": "a.md::0"}],
            "keys": [encode_key("a.md::0")],
        }
    ]
    assert store.deleted == [[encode_key("a.md::0")]]


def test_list_metadata_and_count(fake_azure: type) -> None:
    index = AzureSearchIndex(azure_settings(), NoEmbeddings())
    store = fake_azure.instances[0]
    store.client.results = [
        {"id": encode_key("a.md::0"), "metadata": json.dumps({"source": "a.md"})},
        {"id": encode_key("a.md::1"), "metadata": json.dumps({"source": "a.md"})},
    ]

    assert index.count() == 2
    assert index.list_metadata() == [
        {"source": "a.md", "chunk_id": "a.md::0"},
        {"source": "a.md", "chunk_id": "a.md::1"},
    ]
    assert store.client.search_calls[0]["search_text"] == "*"


def test_reset_deletes_and_recreates_the_index(fake_azure: type) -> None:
    index = AzureSearchIndex(azure_settings(), NoEmbeddings())
    index.reset()
    assert ("delete_index", "runbooks") in FakeIndexClient.calls
    assert len(fake_azure.instances) == 2  # reopened (recreated with our fields)
    assert index.store is fake_azure.instances[1]


# --- Manifest derived from the index ---------------------------------------------------------


def chunk_meta(chunk_id: str, content_hash: str = "h1", fp: str = "fp") -> dict:
    return {
        "source": chunk_id.split("::")[0],
        "chunk_id": chunk_id,
        "content_hash": content_hash,
        "ingested_at": "2026-10-19T10:00:00+00:00",
        "index_fingerprint": fp,
    }


def test_manifest_from_metadata_groups_and_sorts() -> None:
    fp = fingerprint_json(azure_settings())
    manifest = manifest_from_metadata(
        [chunk_meta(f"a.md::{i}", fp=fp) for i in (10, 2, 0, 1)]
        + [chunk_meta("b.md::0", "h2", fp)]
    )
    assert manifest.files["a.md"].chunk_ids == [
        "a.md::0",
        "a.md::1",
        "a.md::2",
        "a.md::10",
    ]
    assert manifest.files["a.md"].content_hash == "h1"
    assert manifest.files["b.md"].chunk_count == 1
    assert manifest.fingerprint == fingerprint(azure_settings())
    assert manifest.chunk_total == 5


def test_manifest_from_metadata_edge_cases() -> None:
    assert manifest_from_metadata([]) is None
    mixed = manifest_from_metadata(
        [chunk_meta("a.md::0", "h1", "fp1"), chunk_meta("a.md::1", "h2", "fp2")]
    )
    assert mixed.files["a.md"].content_hash == ""  # partly applied -> "changed"
    assert mixed.fingerprint == {}  # disagreeing fingerprints -> rebuild


# --- The pipeline on the Azure path (fake index, no Azure) -------------------------------------


class InMemoryIndex:
    """A dict-backed VectorIndex standing in for Azure AI Search."""

    kind = "azure_search"

    def __init__(self) -> None:
        self.chunks: dict[str, dict] = {}

    def as_retriever(self, k: int, metadata_filter: dict | None = None) -> None:
        raise NotImplementedError

    def add_documents(self, docs: list[Document], ids: list[str]) -> None:
        for doc, chunk_id in zip(docs, ids, strict=True):
            self.chunks[chunk_id] = {**doc.metadata, "chunk_id": chunk_id}

    def delete(self, ids: list[str]) -> None:
        for chunk_id in ids:
            self.chunks.pop(chunk_id, None)

    def count(self) -> int:
        return len(self.chunks)

    def list_metadata(self) -> list[dict]:
        return list(self.chunks.values())

    def reset(self) -> None:
        self.chunks.clear()


def test_pipeline_with_index_derived_manifest(tmp_path: Path) -> None:
    runbooks = tmp_path / "runbooks"
    runbooks.mkdir()
    sample = (
        Path(__file__).parent / "../data/runbooks/aws-ec2-unreachable.md"
    ).resolve()
    (runbooks / "ec2.md").write_text(
        sample.read_text(encoding="utf-8"), encoding="utf-8"
    )
    settings = azure_settings(runbooks_dir=runbooks, chroma_dir=tmp_path / "local")
    index = InMemoryIndex()
    created = {"value": False}

    def ingest() -> object:
        def open_index(embeddings: object) -> InMemoryIndex:
            created["value"] = True
            return index

        return run_pipeline(
            settings,
            source=LocalFolderSource(runbooks),
            embeddings_factory=lambda: DeterministicFakeEmbedding(size=8),
            index_factory=open_index,
            count_tokens=lambda texts: (len(texts), True),
            options=PipelineOptions(),
            index_exists=lambda: created["value"],
        )

    first = ingest()
    assert first.exit_code == EXIT_OK
    assert first.full_rebuild
    assert index.count() == first.chunks_added > 0
    assert not settings.manifest_path.exists()  # no local manifest file for Azure
    assert not settings.ingest_lock_path.exists()  # no lock file for Azure

    second = ingest()  # manifest rebuilt from the index: nothing to do
    assert not second.full_rebuild
    assert second.unchanged == ["ec2.md"]
    assert second.chunks_added == 0

    (runbooks / "ec2.md").unlink()
    third = ingest()
    assert third.deleted == ["ec2.md"]
    assert index.count() == 0

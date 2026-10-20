"""Azure AI Search, via LangChain's AzureSearch (package langchain-azure-ai).

- Auth: AZURE_SEARCH_API_KEY if set (local testing), otherwise
  DefaultAzureCredential: Managed Identity in Azure, `az login` locally.
- Index: the library's four required fields (id, content, content_vector,
  metadata JSON) plus our metadata as separate filterable fields, so
  "topic eq 'networking'" works. The index is created on first open.
- Keys: Azure document keys may only contain letters, digits, - _ and =,
  so our chunk IDs ("iis-app-pool-crashes.md::0") are stored URL-safe
  base64-encoded; the original ID is kept in the chunk_id field.
- RETRIEVAL_MODE: vector -> "similarity", hybrid -> "hybrid" (keyword + vector).
"""

import base64
import json
import re
from collections.abc import Sequence

from azure.core.credentials import AzureKeyCredential, TokenCredential
from azure.core.exceptions import ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchableField,
    SearchField,
    SearchFieldDataType,
    SimpleField,
)
from langchain_azure_ai.vectorstores.azuresearch import (
    FIELDS_CONTENT,
    FIELDS_CONTENT_VECTOR,
    FIELDS_ID,
    FIELDS_METADATA,
    AzureSearch,
)
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.retrievers import BaseRetriever

from runbook_assistant.config import Settings
from runbook_assistant.vectorstores.base import METADATA_FIELDS

SEARCH_TYPES = {"vector": "similarity", "hybrid": "hybrid"}
VECTOR_PROFILE = "myHnswProfile"  # the profile AzureSearch creates by default
_FIELD_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def encode_key(chunk_id: str) -> str:
    return base64.urlsafe_b64encode(chunk_id.encode("utf-8")).decode("ascii")


def decode_key(key: str) -> str:
    return base64.urlsafe_b64decode(key.encode("ascii")).decode("utf-8")


def odata_filter(metadata_filter: dict[str, str] | None) -> str | None:
    """{"topic": "networking"} -> "topic eq 'networking'" (OData).

    Field names must be plain identifiers and values are quote-escaped
    ('' inside a string), so a filter value can't change the expression.
    """
    if not metadata_filter:
        return None
    clauses = []
    for field, value in metadata_filter.items():
        if not _FIELD_NAME.match(field):
            raise ValueError(f"invalid filter field name: {field!r}")
        escaped = str(value).replace("'", "''")
        clauses.append(f"{field} eq '{escaped}'")
    return " and ".join(clauses)


def make_credential(settings: Settings) -> AzureKeyCredential | TokenCredential:
    """API key if configured, else DefaultAzureCredential (Managed Identity)."""
    if settings.azure_search_api_key is not None:
        return AzureKeyCredential(settings.azure_search_api_key.get_secret_value())
    # In Container Apps, AZURE_CLIENT_ID (set by Terraform) tells
    # DefaultAzureCredential which user-assigned identity to use (id-app or
    # id-job). It's an Azure SDK convention, read from the environment
    # directly, so it isn't a Settings field.
    return DefaultAzureCredential()


def index_fields(dimensions: int) -> list[SearchField]:
    return [
        SimpleField(
            name=FIELDS_ID, type=SearchFieldDataType.String, key=True, filterable=True
        ),
        SearchableField(name=FIELDS_CONTENT, type=SearchFieldDataType.String),
        SearchField(
            name=FIELDS_CONTENT_VECTOR,
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=dimensions,
            vector_search_profile_name=VECTOR_PROFILE,
        ),
        # Full metadata as JSON (what LangChain returns as Document.metadata).
        SimpleField(name=FIELDS_METADATA, type=SearchFieldDataType.String),
        *(
            SimpleField(name=name, type=SearchFieldDataType.String, filterable=True)
            for name in METADATA_FIELDS
        ),
    ]


class AzureSearchIndex:
    kind = "azure_search"

    def __init__(self, settings: Settings, embeddings: Embeddings) -> None:
        self.settings = settings
        self.embeddings = embeddings
        self.store = self._open()

    def _auth_kwargs(self) -> dict:
        """A key goes in azure_search_key (used by both of AzureSearch's
        clients); otherwise pass the token credential."""
        credential = make_credential(self.settings)
        if isinstance(credential, AzureKeyCredential):
            return {"azure_search_key": credential.key}
        return {"azure_search_key": None, "azure_credential": credential}

    def _open(self) -> AzureSearch:
        """Connect, creating the index with our fields if it doesn't exist."""
        return AzureSearch(
            azure_search_endpoint=self.settings.azure_search_endpoint,
            index_name=self.settings.azure_search_index_name,
            embedding_function=self.embeddings,
            search_type=SEARCH_TYPES[self.settings.retrieval_mode],
            fields=index_fields(self.settings.embedding_dimensions),
            vector_search_dimensions=self.settings.embedding_dimensions,
            **self._auth_kwargs(),
        )

    @staticmethod
    def index_client(settings: Settings) -> SearchIndexClient:
        return SearchIndexClient(
            endpoint=settings.azure_search_endpoint,
            credential=make_credential(settings),
        )

    @classmethod
    def exists(cls, settings: Settings) -> bool:
        """Does the index exist? Checked without creating it."""
        try:
            cls.index_client(settings).get_index(settings.azure_search_index_name)
        except ResourceNotFoundError:
            return False
        return True

    def as_retriever(
        self, k: int, metadata_filter: dict[str, str] | None = None
    ) -> BaseRetriever:
        search_kwargs = {}
        if odata := odata_filter(metadata_filter):
            search_kwargs["filters"] = odata  # AzureSearch expects "filters"
        return self.store.as_retriever(
            search_type=SEARCH_TYPES[self.settings.retrieval_mode],
            k=k,
            search_kwargs=search_kwargs,
        )

    def add_documents(self, docs: Sequence[Document], ids: Sequence[str]) -> None:
        if not docs:
            return
        self.store.add_texts(
            [doc.page_content for doc in docs],
            metadatas=[
                {**doc.metadata, "chunk_id": chunk_id}
                for doc, chunk_id in zip(docs, ids, strict=True)
            ],
            keys=[encode_key(chunk_id) for chunk_id in ids],
        )

    def delete(self, ids: Sequence[str]) -> None:
        if ids:
            self.store.delete(ids=[encode_key(chunk_id) for chunk_id in ids])

    def count(self) -> int:
        return self.store.client.get_document_count()

    def list_metadata(self) -> list[dict]:
        results = self.store.client.search(
            search_text="*", select=[FIELDS_ID, FIELDS_METADATA]
        )
        return [
            {
                **json.loads(result[FIELDS_METADATA] or "{}"),
                "chunk_id": decode_key(result[FIELDS_ID]),
            }
            for result in results
        ]

    def reset(self) -> None:
        """Delete and recreate the index: also safe if the schema changed
        (e.g. a new embedding size)."""
        self.index_client(self.settings).delete_index(
            self.settings.azure_search_index_name
        )
        self.store = self._open()

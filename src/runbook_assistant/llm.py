"""Chat and embedding models from the configured provider (LLM_PROVIDER).

- openai:  api.openai.com, authenticated with OPENAI_API_KEY.
- foundry: Microsoft Foundry in our Azure subscription. Foundry serves the
  OpenAI "v1" API at <endpoint>/openai/v1/, so the same LangChain classes
  work; only the base URL and the credential change. Instead of a key we pass
  a token provider: a function that returns a fresh Entra ID token
  (Managed Identity in Azure, `az login` locally) whenever one is needed.

Everything that needs a model calls get_chat_model() / get_embeddings(), so
switching provider is a config change, not a code change.
"""

from collections.abc import Callable
from functools import lru_cache
from typing import Any

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from runbook_assistant.config import Settings, get_settings


def foundry_base_url(endpoint: str) -> str:
    """https://<name>.cognitiveservices.azure.com/ -> .../openai/v1/"""
    return endpoint.rstrip("/") + "/openai/v1/"


@lru_cache
def foundry_token_provider(scope: str) -> Callable[[], str]:
    """Cached so every model shares one credential and its token cache.

    In Container Apps, AZURE_CLIENT_ID (set by Terraform) tells
    DefaultAzureCredential which user-assigned identity to use.
    """
    return get_bearer_token_provider(DefaultAzureCredential(), scope)


def connection_kwargs(settings: Settings) -> dict[str, Any]:
    """base_url and api_key for the configured provider."""
    if settings.llm_provider == "foundry":
        return {
            "base_url": foundry_base_url(settings.foundry_endpoint or ""),
            "api_key": foundry_token_provider(settings.foundry_token_scope),
        }
    return {"api_key": settings.openai_api_key}


def get_chat_model(model: str) -> ChatOpenAI:
    """A chat model; `model` is a model name (openai) or deployment name (foundry)."""
    return ChatOpenAI(model=model, **connection_kwargs(get_settings()))


def get_embeddings() -> OpenAIEmbeddings:
    """Embedding model shared by ingest and retrieval.

    Both sides must use the same model, otherwise query vectors and stored
    vectors live in different "spaces" and similarity search is meaningless.
    """
    settings = get_settings()
    return OpenAIEmbeddings(
        model=settings.embedding_model,
        # OpenAI-only optimisation (pre-tokenising long texts); with Foundry,
        # send plain strings. Our chunks are far below the model's limit.
        check_embedding_ctx_length=settings.llm_provider == "openai",
        **connection_kwargs(settings),
    )

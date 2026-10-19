"""LangSmith client and tracer with secret masking (the anonymizer).

By default LangChain traces through a hidden global client. To be sure every
trace goes through the anonymizer, runs are invoked with an explicit
LangChainTracer that uses our client; LangChain then doesn't add its default
tracer. evaluate() and feedback use the same client.
"""

from functools import lru_cache

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.tracers import LangChainTracer
from langsmith import Client

from runbook_assistant.config import Settings, get_settings
from runbook_assistant.guardrails.secrets import make_anonymizer


def tracing_enabled(settings: Settings) -> bool:
    return settings.langsmith_tracing and settings.langsmith_api_key is not None


@lru_cache
def get_langsmith_client() -> Client:
    settings = get_settings()
    assert settings.langsmith_api_key is not None
    anonymizer = make_anonymizer(settings) if settings.secrets_masking_enabled else None
    return Client(
        api_key=settings.langsmith_api_key.get_secret_value(), anonymizer=anonymizer
    )


def tracer_callbacks() -> list[BaseCallbackHandler]:
    """Callbacks to pass to .invoke(): our masked tracer, or none if tracing is off."""
    settings = get_settings()
    if not tracing_enabled(settings):
        return []
    return [
        LangChainTracer(
            client=get_langsmith_client(), project_name=settings.langsmith_project
        )
    ]

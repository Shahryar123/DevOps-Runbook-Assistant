"""Application settings loaded from environment variables and the project .env file."""

import os
from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# config.py lives in src/runbook_assistant/, so the project root is two levels up.
PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Typed settings. Field names map to env vars case-insensitively
    (e.g. ``openai_api_key`` <- ``OPENAI_API_KEY``)."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Secrets: SecretStr hides the value in repr/str/logs.
    openai_api_key: SecretStr

    # LangSmith tracing
    langsmith_tracing: bool = False
    langsmith_api_key: SecretStr | None = None
    langsmith_project: str = "runbook-assistant"

    # Models
    answer_model: str = "gpt-5.4-mini"
    router_model: str = "gpt-5.4-mini"
    embedding_model: str = "text-embedding-3-small"

    # Paths
    runbooks_dir: Path = PROJECT_ROOT / "data" / "runbooks"
    chroma_dir: Path = PROJECT_ROOT / "chroma_db"


@lru_cache
def get_settings() -> Settings:
    """Return a single cached Settings instance."""
    return Settings()


def configure_tracing(settings: Settings) -> None:
    """Export LangSmith settings to os.environ.

    pydantic-settings reads .env into the Settings object but does not set
    environment variables. LangChain's LangSmith integration reads LANGSMITH_*
    from os.environ, so we copy them there explicitly.
    """
    if not settings.langsmith_tracing or settings.langsmith_api_key is None:
        os.environ["LANGSMITH_TRACING"] = "false"
        return
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key.get_secret_value()
    os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project

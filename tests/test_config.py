import pytest

from runbook_assistant.config import PROJECT_ROOT, ConfigError, Settings, load_settings


def test_settings_load_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ANSWER_MODEL", "custom-model")

    # _env_file=None ignores the real .env so the test is hermetic.
    settings = Settings(_env_file=None)

    assert settings.openai_api_key.get_secret_value() == "sk-test"
    assert "sk-test" not in repr(settings)
    assert settings.answer_model == "custom-model"
    assert settings.runbooks_dir == PROJECT_ROOT / "data" / "runbooks"


# --- 12-factor config: environment only, one clear error -----------------------------

CONFIG_VARS = (
    "OPENAI_API_KEY",
    "VECTOR_STORE",
    "RETRIEVAL_MODE",
    "AZURE_SEARCH_ENDPOINT",
    "AZURE_SEARCH_INDEX_NAME",
    "AZURE_SEARCH_API_KEY",
    "LANGSMITH_TRACING",
    "LANGSMITH_API_KEY",
    "LLM_PROVIDER",
    "FOUNDRY_ENDPOINT",
    "MODERATION_ENABLED",
)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """No config from the machine running the tests."""
    for name in CONFIG_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_starts_from_environment_only(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("OPENAI_API_KEY", "sk-test")
    clean_env.setenv("VECTOR_STORE", "azure_search")
    clean_env.setenv("RETRIEVAL_MODE", "hybrid")
    clean_env.setenv("AZURE_SEARCH_ENDPOINT", "https://x.search.windows.net")
    clean_env.setenv("AZURE_SEARCH_INDEX_NAME", "runbooks")
    clean_env.setenv("RETRIEVER_K", "6")
    clean_env.setenv("RUNBOOK_EXPECTED_SECTIONS", '["Symptoms"]')  # lists as JSON

    settings = load_settings(_env_file=None)  # as in a container: no .env

    assert settings.vector_store == "azure_search"
    assert settings.retrieval_mode == "hybrid"
    assert settings.retriever_k == 6
    assert settings.runbook_expected_sections == ["Symptoms"]
    assert settings.azure_search_api_key is None  # -> DefaultAzureCredential


def test_one_error_lists_every_missing_setting(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("VECTOR_STORE", "azure_search")
    clean_env.setenv("LANGSMITH_TRACING", "true")

    with pytest.raises(ConfigError) as error:
        load_settings(_env_file=None)

    message = str(error.value)
    for name in (
        "OPENAI_API_KEY",
        "AZURE_SEARCH_ENDPOINT",
        "AZURE_SEARCH_INDEX_NAME",
        "LANGSMITH_API_KEY",
    ):
        assert name in message
    assert message.count("\n  - ") == 1  # one error, not one per field


def test_hybrid_needs_azure_search(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("OPENAI_API_KEY", "sk-test")
    clean_env.setenv("RETRIEVAL_MODE", "hybrid")
    with pytest.raises(ConfigError, match="RETRIEVAL_MODE=hybrid needs VECTOR_STORE"):
        load_settings(_env_file=None)


def test_invalid_values_are_named(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("OPENAI_API_KEY", "sk-test")
    clean_env.setenv("VECTOR_STORE", "pinecone")
    with pytest.raises(ConfigError, match="VECTOR_STORE"):
        load_settings(_env_file=None)


def test_defaults_are_chroma_and_vector(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("OPENAI_API_KEY", "sk-test")
    settings = load_settings(_env_file=None)
    assert settings.vector_store == "chroma"
    assert settings.retrieval_mode == "vector"
    assert settings.llm_provider == "openai"


def test_foundry_needs_endpoint(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("OPENAI_API_KEY", "sk-test")
    clean_env.setenv("LLM_PROVIDER", "foundry")
    with pytest.raises(ConfigError, match="FOUNDRY_ENDPOINT"):
        load_settings(_env_file=None)


def test_foundry_still_needs_openai_key_for_moderation(
    clean_env: pytest.MonkeyPatch,
) -> None:
    clean_env.setenv("LLM_PROVIDER", "foundry")
    clean_env.setenv("FOUNDRY_ENDPOINT", "https://x.cognitiveservices.azure.com/")
    with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
        load_settings(_env_file=None)

    clean_env.setenv("MODERATION_ENABLED", "false")
    settings = load_settings(_env_file=None)  # no OpenAI key needed at all
    assert settings.openai_api_key is None

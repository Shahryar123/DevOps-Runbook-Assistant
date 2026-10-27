import pytest

from runbook_assistant import llm
from runbook_assistant.config import Settings

ENDPOINT = "https://aif-x.cognitiveservices.azure.com/"


def make_settings(**overrides: object) -> Settings:
    return Settings(openai_api_key="sk-test", _env_file=None, **overrides)


def test_foundry_base_url() -> None:
    expected = "https://aif-x.cognitiveservices.azure.com/openai/v1/"
    assert llm.foundry_base_url(ENDPOINT) == expected
    assert llm.foundry_base_url(ENDPOINT.rstrip("/")) == expected


def test_openai_uses_the_api_key() -> None:
    kwargs = llm.connection_kwargs(make_settings())
    assert kwargs["api_key"].get_secret_value() == "sk-test"
    assert "base_url" not in kwargs


def test_foundry_uses_a_token_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_provider(scope: str) -> object:
        return lambda: f"token-for:{scope}"

    monkeypatch.setattr(llm, "foundry_token_provider", fake_provider)
    settings = make_settings(llm_provider="foundry", foundry_endpoint=ENDPOINT)

    kwargs = llm.connection_kwargs(settings)

    assert kwargs["base_url"].endswith("/openai/v1/")
    assert (
        kwargs["api_key"]() == "token-for:https://cognitiveservices.azure.com/.default"
    )


def test_chat_model_targets_foundry(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_settings(llm_provider="foundry", foundry_endpoint=ENDPOINT)
    monkeypatch.setattr(llm, "get_settings", lambda: settings)
    monkeypatch.setattr(llm, "foundry_token_provider", lambda scope: lambda: "t")

    model = llm.get_chat_model("gpt-5.4-mini")
    embeddings = llm.get_embeddings()

    assert model.model_name == "gpt-5.4-mini"  # = the deployment name
    assert model.openai_api_base == llm.foundry_base_url(ENDPOINT)
    assert embeddings.check_embedding_ctx_length is False

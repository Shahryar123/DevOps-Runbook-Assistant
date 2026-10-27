"""Azure AI Content Safety: HTTP calls, Azure moderation, Prompt Shields."""

import httpx
import pytest

from runbook_assistant.config import Settings
from runbook_assistant.guardrails import content_safety, moderation, prompt_shields
from runbook_assistant.guardrails.content_safety import ShieldResult

ENDPOINT = "https://aif-x.cognitiveservices.azure.com/"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        llm_provider="foundry",
        foundry_endpoint=ENDPOINT,
        moderation_provider="azure",
        prompt_shields_enabled=True,
    )


@pytest.fixture
def http(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Fake httpx.post and token: record the request, return calls["reply"]."""
    calls: dict = {"requests": [], "reply": {}}

    def fake_post(url: str, **kwargs: object) -> httpx.Response:
        calls["requests"].append({"url": url, **kwargs})
        return httpx.Response(
            200, json=calls["reply"], request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(content_safety.httpx, "post", fake_post)
    monkeypatch.setattr(
        content_safety, "foundry_token_provider", lambda scope: lambda: "tok"
    )
    return calls


def test_no_openai_key_needed_with_foundry_and_azure(settings: Settings) -> None:
    assert settings.openai_api_key is None
    assert not settings.uses_openai


def test_analyze_text_request_and_parsing(settings: Settings, http: dict) -> None:
    http["reply"] = {
        "categoriesAnalysis": [
            {"category": "Hate", "severity": 0},
            {"category": "Violence", "severity": 4},
        ]
    }
    assert content_safety.analyze_text("hi", settings) == {"Hate": 0, "Violence": 4}

    request = http["requests"][0]
    assert request["url"] == f"{ENDPOINT}contentsafety/text:analyze"
    assert request["params"] == {"api-version": "2024-09-01"}
    assert request["headers"] == {"Authorization": "Bearer tok"}
    assert request["json"]["outputType"] == "FourSeverityLevels"


def test_shield_prompt_parsing(settings: Settings, http: dict) -> None:
    http["reply"] = {
        "userPromptAnalysis": {"attackDetected": True},
        "documentsAnalysis": [{"attackDetected": False}, {"attackDetected": True}],
    }
    result = content_safety.shield_prompt("q", ["a", "b"], settings)
    assert result == ShieldResult(True, [False, True])
    assert http["requests"][0]["url"].endswith("contentsafety/text:shieldPrompt")


def test_http_errors_raise(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(url: str, **_: object) -> httpx.Response:
        return httpx.Response(403, request=httpx.Request("POST", url))

    monkeypatch.setattr(content_safety.httpx, "post", forbidden)
    monkeypatch.setattr(
        content_safety, "foundry_token_provider", lambda scope: lambda: "tok"
    )
    with pytest.raises(httpx.HTTPStatusError):
        content_safety.analyze_text("hi", settings)


@pytest.mark.parametrize(
    ("severities", "flagged"),
    [
        ({"Violence": 2, "Hate": 0}, []),  # "kill the process": low, allowed
        ({"Violence": 4, "Hate": 6}, ["hate (severity 6)", "violence (severity 4)"]),
    ],
)
def test_azure_moderation_threshold(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    severities: dict[str, int],
    flagged: list[str],
) -> None:
    monkeypatch.setattr(moderation, "analyze_text", lambda text, s: severities)
    result = moderation.moderate_azure("text", settings)
    assert result == (bool(flagged), flagged)


def test_batches_respect_both_limits() -> None:
    texts = ["a" * 4, "b" * 4, "c" * 4, "d" * 20, "e"]
    assert [len(b) for b in prompt_shields.batches(texts, 2, 10)] == [2, 1, 1, 1]
    assert list(prompt_shields.batches([], 5, 10)) == []


def test_scan_documents_keeps_order(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_shield(prompt: str, docs: list[str], _: Settings) -> ShieldResult:
        return ShieldResult(False, ["evil" in d for d in docs])

    monkeypatch.setattr(prompt_shields, "shield_prompt", fake_shield)
    small = settings.model_copy(update={"prompt_shields_max_documents": 2})
    texts = ["ok", "evil text", "ok", "ok", "evil"]
    assert prompt_shields.scan_documents(texts, small) == [
        False,
        True,
        False,
        False,
        True,
    ]


def test_scan_documents_rejects_a_short_answer(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        prompt_shields, "shield_prompt", lambda *_: ShieldResult(False, [])
    )
    with pytest.raises(ValueError, match="0 results for 1"):
        prompt_shields.scan_documents(["text"], settings)

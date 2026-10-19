"""UI-facing service logic. No OpenAI, LangSmith or Chroma calls."""

from pathlib import Path
from uuid import uuid4

import httpx
import openai
import pytest
from langchain_core.documents import Document

from runbook_assistant import service
from runbook_assistant.config import Settings
from runbook_assistant.graph.build import GraphOptions
from runbook_assistant.rag import NO_ANSWER

DOC = Document(page_content="chunk", metadata={"source": "a.md", "section": "s"})


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    fake = Settings(openai_api_key="sk-test", _env_file=None, langsmith_tracing=False)
    monkeypatch.setattr(service, "get_settings", lambda: fake)
    return fake


@pytest.fixture
def tracing(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> Settings:
    """Tracing on, with the LangSmith project lookup faked."""
    monkeypatch.setattr(settings, "langsmith_tracing", True)
    monkeypatch.setattr(settings, "langsmith_api_key", "ls-test")
    monkeypatch.setattr(service, "project_path", lambda _: "o/org1/projects/p/proj1")
    return settings


# --- Setups ----------------------------------------------------------------------


def test_options_for_setup(settings: Settings) -> None:
    assert service.options_for_setup("chain", settings) is None
    assert service.options_for_setup("graph", settings) == GraphOptions(
        routing_enabled=False,
        grading_enabled=settings.grading_enabled,
        max_retries=settings.max_retries,
    )
    assert service.options_for_setup("graph_routing", settings).routing_enabled
    with pytest.raises(ValueError, match="unknown setup"):
        service.options_for_setup("nope", settings)


def test_default_ui_setup_is_valid(settings: Settings) -> None:
    assert settings.ui_default_setup in service.SETUPS


# --- answer() ---------------------------------------------------------------------


def test_answer_graph_maps_result_and_tags_ui_runs(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    run_id = uuid4()
    calls: list[tuple] = []

    def fake_ask_graph(question, options, tags):
        calls.append((question, options, tags))
        return {
            "answer": "Restart the pool (source: a.md)",
            "topic": "deployment",
            "search_queries": [question, "rewritten"],
            "retry_count": 1,
            "kept": [DOC],
            "filtered_out": [DOC, DOC],
            "run_id": run_id,
            "latency_s": 1.5,
            "question_sent": question,
            "answer_body": "Restart the pool (source: a.md)",
            "command_warnings": [],
            "guardrail_events": [],
            "blocked_reason": None,
        }

    monkeypatch.setattr(service, "ask_graph", fake_ask_graph)
    result = service.answer("pool stops", "graph_routing")

    assert calls == [
        (
            "pool stops",
            service.options_for_setup("graph_routing", settings),
            settings.ui_tags,
        )
    ]
    assert result["topic"] == "deployment"
    assert result["search_queries"] == ["pool stops", "rewritten"]
    assert result["retry_count"] == 1
    assert len(result["kept"]) == 1
    assert len(result["filtered_out"]) == 2
    assert result["run_id"] == str(run_id)
    assert result["latency_s"] == 1.5
    assert result["is_refusal"] is False
    assert result["trace_url"] is None  # tracing off


def test_answer_chain_has_no_routing_or_retries(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    def fake_ask_with_sources(question, tags):
        assert tags == settings.ui_tags
        return {
            "answer": NO_ANSWER,
            "docs": [DOC],
            "run_id": uuid4(),
            "latency_s": 0.4,
            "question_sent": "kubernetes? token=[MASKED_SECRET]",
            "guardrail_events": [],
        }

    monkeypatch.setattr(service, "ask_with_sources", fake_ask_with_sources)
    result = service.answer("kubernetes? token=abc123!x", "chain")

    assert result["question"] == "kubernetes? token=[MASKED_SECRET]"
    assert result["topic"] == service.NO_ROUTING_TOPIC
    assert result["search_queries"] == ["kubernetes? token=[MASKED_SECRET]"]
    assert result["command_warnings"] == []
    assert result["retry_count"] == 0
    assert result["kept"] == [DOC]
    assert result["filtered_out"] == []
    assert result["is_refusal"] is True


# --- Trace links and feedback ------------------------------------------------------


def test_trace_url_built_from_config(tracing: Settings) -> None:
    tracing.langsmith_web_url = "https://eu.smith.langchain.com/"
    assert service.trace_url("run1") == (
        "https://eu.smith.langchain.com/o/org1/projects/p/proj1/r/run1?poll=true"
    )


def test_trace_url_none_when_tracing_off(settings: Settings) -> None:
    assert service.trace_url("run1") is None


def test_trace_url_none_when_lookup_fails(
    monkeypatch: pytest.MonkeyPatch, tracing: Settings
) -> None:
    def fail(_: str) -> str:
        raise RuntimeError("LangSmith down")

    monkeypatch.setattr(service, "project_path", fail)
    assert service.trace_url("run1") is None


def test_send_feedback_uses_configured_key(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    sent: list[dict] = []

    class FakeClient:
        def create_feedback(self, **kwargs: object) -> None:
            sent.append(kwargs)

    monkeypatch.setattr(service, "get_langsmith_client", FakeClient)
    run_id = uuid4()
    service.send_feedback(str(run_id), 1, "  ")

    assert sent == [
        {
            "run_id": run_id,
            "trace_id": run_id,
            "key": settings.feedback_key,
            "score": 1,
            "comment": None,  # blank comment is not sent
        }
    ]


# --- Startup checks and errors -------------------------------------------------------


def test_runbook_index_missing_folder_does_not_open_chroma(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, tmp_path: Path
) -> None:
    def fail() -> list:
        raise AssertionError("must not open Chroma")

    monkeypatch.setattr(service, "list_runbooks", fail)
    settings.chroma_dir = tmp_path / "missing"
    assert service.runbook_index(settings) == ("missing", [])


def test_runbook_index_empty_store(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, tmp_path: Path
) -> None:
    monkeypatch.setattr(service, "list_runbooks", list)
    settings.chroma_dir = tmp_path
    assert service.runbook_index(settings) == ("empty", [])


REQUEST = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


def status_error(cls: type[openai.APIStatusError], code: int) -> Exception:
    return cls("boom", response=httpx.Response(code, request=REQUEST), body=None)


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (status_error(openai.RateLimitError, 429), "rate limiting"),
        (openai.APITimeoutError(request=REQUEST), "too long"),
        (openai.APIConnectionError(request=REQUEST), "Couldn't reach OpenAI"),
        (status_error(openai.AuthenticationError, 401), "API key"),
        (status_error(openai.InternalServerError, 500), "OpenAI returned an error"),
        (ValueError("bug"), "Something went wrong"),
    ],
)
def test_friendly_error(exc: Exception, expected: str) -> None:
    message = service.friendly_error(exc)
    assert expected in message
    assert "boom" not in message  # raw details stay in the log

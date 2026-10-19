"""Streamlit app tests with AppTest: runs app.py in-process, no browser.
No OpenAI, LangSmith or Chroma calls (the service layer is faked)."""

from collections.abc import Iterator
from pathlib import Path

import pytest
import streamlit as st
from langchain_core.documents import Document
from streamlit.testing.v1 import AppTest

from runbook_assistant import service
from runbook_assistant.config import PROJECT_ROOT, get_settings
from runbook_assistant.rag import NO_ANSWER
from runbook_assistant.retriever import RunbookInfo

DOC = Document(
    page_content="Run Start-WebAppPool.",
    metadata={"source": "a.md", "title": "IIS App Pool Crashes", "section": "Fix"},
)
APP = PROJECT_ROOT / "src" / "runbook_assistant" / "app.py"


@pytest.fixture
def fresh_settings() -> Iterator[None]:
    """Settings and Streamlit caches are process-wide; reset around the test."""
    get_settings.cache_clear()
    st.cache_data.clear()
    yield
    get_settings.cache_clear()
    st.cache_data.clear()


def test_missing_index_shows_ingest_help_instead_of_crashing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fresh_settings: None
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("CHROMA_DIR", str(tmp_path / "missing"))

    app = AppTest.from_file(str(APP), default_timeout=60).run()

    assert not app.exception
    assert "No search database was found" in app.error[0].value
    assert "runbook_assistant.ingest" in app.code[0].value
    assert not app.chat_input  # stopped before the chat was drawn
    assert not (tmp_path / "missing").exists()  # Chroma didn't create it


@pytest.fixture
def fake_service(
    monkeypatch: pytest.MonkeyPatch, fresh_settings: None
) -> dict[str, list]:
    """App with a ready index and a fake service: the real UI code runs,
    but no question reaches OpenAI and no feedback reaches LangSmith."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    calls: dict[str, list] = {"answer": [], "feedback": []}
    runbook = RunbookInfo(title="IIS App Pool Crashes", source="a.md", topic="x")

    def fake_answer(question: str, setup: str) -> dict:
        calls["answer"].append((question, setup))
        answer = NO_ANSWER if "kubernetes" in question else "Restart the pool."
        return {
            "question": question,
            "setup": setup,
            "answer": answer,
            "is_refusal": answer == NO_ANSWER,
            "topic": "deployment",
            "search_queries": [question, "rewritten query"],
            "retry_count": 1,
            "kept": [] if answer == NO_ANSWER else [DOC],
            "filtered_out": [DOC],
            "run_id": "run-1",
            "latency_s": 1.23,
            "trace_url": "https://smith.example/r/run-1",
        }

    monkeypatch.setattr(service, "runbook_index", lambda _: ("ok", [runbook]))
    monkeypatch.setattr(service, "load_setup", lambda _: None)
    monkeypatch.setattr(service, "answer", fake_answer)
    monkeypatch.setattr(service, "tracing_enabled", lambda _: True)
    monkeypatch.setattr(
        service, "send_feedback", lambda *args: calls["feedback"].append(args)
    )
    return calls


def ask(app: AppTest, question: str) -> AppTest:
    return app.chat_input[0].set_value(question).run()


def test_chat_answer_sources_and_behind_the_scenes(fake_service: dict) -> None:
    app = AppTest.from_file(str(APP), default_timeout=60).run()
    assert app.radio[0].value == "graph_routing"  # default setup from config
    assert "IIS App Pool Crashes" in app.sidebar.markdown[-1].value

    app = ask(app, "pool keeps stopping")

    assert not app.exception
    assert fake_service["answer"] == [("pool keeps stopping", "graph_routing")]
    assert [m.name for m in app.chat_message] == ["user", "assistant"]
    labels = [e.label for e in app.expander]
    assert labels == ["Sources (1)", "Behind the scenes"]
    details = " ".join(m.value for m in app.expander[1].markdown)
    assert "rewritten query" in details
    assert "1 kept, 1 filtered out" in details


def test_refusal_is_styled_as_warning(fake_service: dict) -> None:
    app = ask(AppTest.from_file(str(APP), default_timeout=60).run(), "kubernetes?")
    assert app.warning[0].value == NO_ANSWER


def test_too_long_question_is_not_sent(fake_service: dict) -> None:
    app = AppTest.from_file(str(APP), default_timeout=60).run()
    app = ask(app, "x" * 501)
    assert fake_service["answer"] == []
    assert "Please keep it under 500" in app.error[0].value


def test_setup_selector_and_clear_chat(fake_service: dict) -> None:
    app = AppTest.from_file(str(APP), default_timeout=60).run()
    app.radio[0].set_value("chain").run()
    app = ask(app, "pool keeps stopping")
    assert fake_service["answer"][-1][1] == "chain"

    app.sidebar.button[0].click().run()
    assert not app.chat_message


def test_feedback_is_sent_once_then_locked(fake_service: dict) -> None:
    app = ask(AppTest.from_file(str(APP), default_timeout=60).run(), "pool stops")
    assert app.link_button[0].proto.url == "https://smith.example/r/run-1"

    app.feedback[0].set_value(1).run()  # thumbs up shows the comment form
    # Form values are only sent on submit, so type and click in the same run.
    app.text_input[0].set_value("worked")
    app.button[0].click().run()  # "Send feedback" (form submit)

    assert fake_service["feedback"] == [("run-1", 1, "worked")]
    assert "feedback was sent" in app.caption[-1].value
    assert not app.text_input  # form gone: no second submit possible
    assert app.feedback[0].proto.disabled

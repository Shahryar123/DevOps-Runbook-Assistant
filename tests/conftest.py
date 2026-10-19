"""Shared test fixtures."""

import pytest
from langchain_core.documents import Document
from langchain_core.runnables import RunnableLambda

from runbook_assistant.config import Settings
from runbook_assistant.graph import build, nodes
from runbook_assistant.guardrails import input as input_guardrails


@pytest.fixture(autouse=True)
def no_guardrail_api_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any test that reaches the moderation API or the injection classifier.

    pytest.fail raises a BaseException, so the guardrails' fail-open
    `except Exception` can't swallow it. Tests that need these checks
    monkeypatch them with fakes.
    """

    def network_call(*_: object) -> None:
        pytest.fail("test reached a guardrail API call; fake it")

    monkeypatch.setattr(input_guardrails, "moderate", network_call)
    monkeypatch.setattr(input_guardrails, "classify", network_call)


def make_doc(text: str) -> Document:
    return Document(
        page_content=text,
        metadata={"source": "sample.md", "topic": "networking", "section": "Symptoms"},
    )


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Settings with a dummy key, injected into the nodes and build modules.

    The guardrails that call an API (moderation, injection classifier) are
    off; the free keyword check and the output guardrails still run.
    """
    fake = Settings(
        openai_api_key="sk-test",
        _env_file=None,
        moderation_enabled=False,
        injection_classifier_enabled=False,
    )
    monkeypatch.setattr(nodes, "get_settings", lambda: fake)
    monkeypatch.setattr(build, "get_settings", lambda: fake)
    return fake


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> dict:
    """Replace every LLM/vector-store factory with a fake and record calls.

    Default behaviour: router says "networking", retriever returns one chunk
    echoing the query, grader rejects everything. Tests override as needed.
    """
    route_model = nodes.make_route_model(settings.router_topics)
    calls: dict = {"filters": [], "queries": [], "generator_inputs": []}

    def fake_get_retriever(metadata_filter: dict | None = None) -> RunnableLambda:
        calls["filters"].append(metadata_filter)

        def search(query: str) -> list[Document]:
            calls["queries"].append(query)
            return [make_doc(f"chunk for: {query}")]

        return RunnableLambda(search)

    def fake_generate(inputs: dict) -> str:
        calls["generator_inputs"].append(inputs)
        return f"answer to: {inputs['question']}"

    monkeypatch.setattr(
        nodes,
        "get_router",
        lambda: RunnableLambda(lambda _: route_model(topic="networking")),
    )
    monkeypatch.setattr(nodes, "get_retriever", fake_get_retriever)
    monkeypatch.setattr(
        nodes,
        "get_grader",
        lambda: RunnableLambda(lambda _: nodes.GradeDocument(binary_score="no")),
    )
    monkeypatch.setattr(
        nodes,
        "get_rewriter",
        lambda: RunnableLambda(lambda x: f"rewritten {x['search_query']}"),
    )
    monkeypatch.setattr(nodes, "get_generator", lambda: RunnableLambda(fake_generate))
    return calls

"""Graph wiring, routing, and loop tests. No OpenAI calls and no .env needed.

LLM-backed pieces (router, retriever, grader, rewriter, generator) are
replaced with RunnableLambda fakes (the `settings` and `fakes` fixtures in
conftest.py), so the real node and graph logic runs.
"""

from uuid import uuid4

import pytest
from langchain_core.runnables import RunnableLambda
from pydantic import ValidationError

from runbook_assistant.config import Settings
from runbook_assistant.graph import build, nodes
from runbook_assistant.graph.build import (
    GraphOptions,
    build_graph,
    decide_next_step,
    recursion_limit,
)
from runbook_assistant.rag import NO_ANSWER
from tests.conftest import make_doc


def run(
    question: str,
    max_retries: int,
    routing_enabled: bool = True,
    grading_enabled: bool = True,
) -> dict:
    options = GraphOptions(
        routing_enabled=routing_enabled,
        grading_enabled=grading_enabled,
        max_retries=max_retries,
    )
    return build_graph(options).invoke(
        {"question": question},
        config={"recursion_limit": recursion_limit(max_retries)},
    )


# --- decide_next_step (pure) -------------------------------------------------


def test_decide_generates_when_any_chunk_is_relevant() -> None:
    state = {"relevant_documents": [make_doc("x")], "retry_count": 0}
    assert decide_next_step(state, max_retries=1) == "generate"


def test_decide_retries_when_nothing_relevant_and_retries_left() -> None:
    state = {"relevant_documents": [], "retry_count": 0}
    assert decide_next_step(state, max_retries=1) == "rewrite_query"


def test_decide_gives_up_when_no_retries_left() -> None:
    state = {"relevant_documents": [], "retry_count": 1}
    assert decide_next_step(state, max_retries=1) == "no_answer"
    assert decide_next_step({"relevant_documents": []}, max_retries=0) == "no_answer"


# --- Graph wiring --------------------------------------------------------------


def test_graph_compiles_with_expected_nodes_and_edges() -> None:
    drawable = build_graph(GraphOptions()).get_graph()

    assert set(drawable.nodes) == {
        "__start__",
        "input_guardrails",
        "blocked_reply",
        "route_question",
        "off_topic_reply",
        "retrieve",
        "grade_documents",
        "rewrite_query",
        "generate",
        "output_guardrails",
        "no_answer",
        "__end__",
    }
    edges = {(e.source, e.target) for e in drawable.edges}
    assert edges == {
        ("__start__", "input_guardrails"),
        ("input_guardrails", "blocked_reply"),
        ("input_guardrails", "route_question"),
        ("route_question", "off_topic_reply"),
        ("route_question", "retrieve"),
        ("retrieve", "grade_documents"),
        ("grade_documents", "generate"),
        ("grade_documents", "rewrite_query"),
        ("grade_documents", "no_answer"),
        ("rewrite_query", "retrieve"),
        ("generate", "output_guardrails"),
        ("output_guardrails", "__end__"),
        ("no_answer", "__end__"),
        ("blocked_reply", "__end__"),
        ("off_topic_reply", "__end__"),
    }


# --- Retry loop ------------------------------------------------------------------


@pytest.mark.parametrize("max_retries", [0, 1, 3])
def test_retry_limit_is_respected(fakes: dict, max_retries: int) -> None:
    state = run("q", max_retries)

    assert state["answer"] == NO_ANSWER
    assert state["retry_count"] == max_retries
    assert len(state["search_queries"]) == max_retries + 1
    assert len(fakes["queries"]) == max_retries + 1  # one search per attempt
    assert fakes["generator_inputs"] == []  # never generated


def test_first_attempt_filters_by_topic_retries_search_everything(
    fakes: dict,
) -> None:
    run("q", max_retries=2)

    assert fakes["filters"] == [{"topic": "networking"}, None, None]


def test_generate_answers_original_question_after_rewrite(
    fakes: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Grader only accepts chunks found by the rewritten query.
    def grade(inputs: dict) -> nodes.GradeDocument:
        score = "yes" if "rewritten" in inputs["document"] else "no"
        return nodes.GradeDocument(binary_score=score)

    monkeypatch.setattr(nodes, "get_grader", lambda: RunnableLambda(grade))

    state = run("website dead after new build", max_retries=1)

    assert state["search_queries"] == [
        "website dead after new build",
        "rewritten website dead after new build",
    ]
    assert state["retry_count"] == 1
    assert len(state["filtered_documents"]) == 1  # rejected on attempt 1
    assert state["answer"] == "answer to: website dead after new build"
    assert fakes["generator_inputs"][0]["question"] == "website dead after new build"


# --- Feature flags -----------------------------------------------------------------


def fail(*_: object) -> None:
    raise AssertionError("this LLM must not be called")


def test_routing_disabled_skips_router_and_searches_everything(
    fakes: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(nodes, "get_router", fail)

    state = run("q", max_retries=0, routing_enabled=False)

    assert state["topic"] == "general"
    assert fakes["filters"] == [None]


def test_grading_disabled_keeps_everything_and_generates(
    fakes: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(nodes, "get_grader", fail)

    state = run("q", max_retries=1, grading_enabled=False)

    assert len(state["relevant_documents"]) == 1
    assert state["filtered_documents"] == []
    assert state["retry_count"] == 0
    assert state["answer"] == "answer to: q"


# --- Nodes ------------------------------------------------------------------------


def test_topic_filter() -> None:
    assert nodes.topic_filter("security", 0, "general") == {"topic": "security"}
    assert nodes.topic_filter("security", 1, "general") is None
    assert nodes.topic_filter("general", 0, "general") is None
    assert nodes.topic_filter(None, 0, "general") is None


def test_no_answer_returns_fixed_text() -> None:
    assert nodes.no_answer({"question": "anything"}) == {"answer": NO_ANSWER}


def test_grade_documents_splits_kept_and_filtered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_grade(inputs: dict) -> nodes.GradeDocument:
        score = "yes" if "iis" in inputs["document"] else "no"
        return nodes.GradeDocument(binary_score=score)

    monkeypatch.setattr(nodes, "get_grader", lambda: RunnableLambda(fake_grade))
    docs = [make_doc("iis pool"), make_doc("aws ec2"), make_doc("iis 5002")]

    update = nodes.grade_documents({"question": "q", "documents": docs})

    assert update == {
        "relevant_documents": [docs[0], docs[2]],
        "filtered_documents": [docs[1]],
    }


# --- Router schema ------------------------------------------------------------------


def test_router_schema_only_allows_configured_topics(settings: Settings) -> None:
    route_model = nodes.make_route_model(settings.router_topics)

    for topic in settings.router_topics:
        assert route_model(topic=topic).topic == topic
    with pytest.raises(ValidationError):
        route_model(topic="cooking")

    # The JSON schema sent to the model is restricted to the same values.
    enum = route_model.model_json_schema()["properties"]["topic"]["enum"]
    assert enum == [*settings.runbook_topics, "general", "off_topic"]


def test_off_topic_not_offered_when_disabled() -> None:
    settings = Settings(
        openai_api_key="sk-test", _env_file=None, off_topic_enabled=False
    )
    assert "off_topic" not in settings.router_topics


def test_ask_graph_uses_given_run_id_and_adds_tags(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """The caller's run_id becomes the LangSmith root run ID (for trace links
    and feedback), and extra tags are added to the configured ones."""
    seen: dict = {}

    class FakeGraph:
        def invoke(self, state: dict, config: dict) -> dict:
            seen.update(config)
            return {
                "answer": "a",
                "topic": "networking",
                "search_queries": [state["question"]],
                "retry_count": 0,
            }

    monkeypatch.setenv("LANGSMITH_TRACING", "false")  # restored after the test
    monkeypatch.setattr(build, "get_graph", lambda _: FakeGraph())
    run_id = uuid4()

    result = build.ask_graph("q", GraphOptions(), run_id=run_id, tags=["ui"])

    assert seen["run_id"] == run_id
    assert seen["tags"] == [*settings.graph_tags, "ui"]
    assert result["run_id"] == run_id
    assert result["latency_s"] >= 0
    assert result["kept"] == []

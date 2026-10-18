"""Graph wiring and routing tests. No OpenAI calls and no .env needed."""

import pytest
from langchain_core.documents import Document
from langchain_core.runnables import RunnableLambda

from runbook_assistant.graph import nodes
from runbook_assistant.graph.build import build_graph, decide_next_step
from runbook_assistant.rag import NO_ANSWER


def make_doc(text: str) -> Document:
    return Document(
        page_content=text, metadata={"source": "sample.md", "section": "Symptoms"}
    )


def test_decide_next_step_generates_when_any_chunk_is_relevant() -> None:
    state = {"relevant_documents": [make_doc("relevant")]}
    assert decide_next_step(state) == "generate"


def test_decide_next_step_no_answer_when_nothing_is_relevant() -> None:
    assert decide_next_step({"relevant_documents": []}) == "no_answer"
    assert decide_next_step({}) == "no_answer"


def test_graph_compiles_with_expected_nodes_and_edges() -> None:
    drawable = build_graph().get_graph()

    assert set(drawable.nodes) == {
        "__start__",
        "retrieve",
        "grade_documents",
        "generate",
        "no_answer",
        "__end__",
    }
    edges = {(e.source, e.target) for e in drawable.edges}
    assert edges == {
        ("__start__", "retrieve"),
        ("retrieve", "grade_documents"),
        ("grade_documents", "generate"),
        ("grade_documents", "no_answer"),
        ("generate", "__end__"),
        ("no_answer", "__end__"),
    }


def test_no_answer_returns_fixed_text() -> None:
    assert nodes.no_answer({"question": "anything"}) == {"answer": NO_ANSWER}


def test_grade_documents_keeps_only_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_grade(inputs: dict) -> nodes.GradeDocument:
        score = "yes" if "iis" in inputs["document"] else "no"
        return nodes.GradeDocument(binary_score=score)

    monkeypatch.setattr(nodes, "get_grader", lambda: RunnableLambda(fake_grade))
    docs = [make_doc("iis pool"), make_doc("aws ec2"), make_doc("iis 5002")]

    update = nodes.grade_documents({"question": "q", "documents": docs})

    assert update == {"relevant_documents": [docs[0], docs[2]]}

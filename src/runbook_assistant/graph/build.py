"""Build the LangGraph: retrieve -> grade -> generate | no_answer."""

from functools import lru_cache
from typing import Literal, TypedDict

from langchain_core.documents import Document
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from runbook_assistant.config import configure_tracing, get_settings
from runbook_assistant.graph.nodes import (
    generate,
    grade_documents,
    no_answer,
    retrieve,
)
from runbook_assistant.graph.state import GraphState


class GraphResult(TypedDict):
    answer: str
    kept: list[Document]
    filtered_out: list[Document]


def decide_next_step(state: GraphState) -> Literal["generate", "no_answer"]:
    """Routing function for the conditional edge after grading.

    Pure function of the state (no API calls), so it is easy to unit test.
    """
    return "generate" if state.get("relevant_documents") else "no_answer"


def build_graph() -> CompiledStateGraph:
    """Wire nodes and edges, then compile.

    - StateGraph(GraphState): a graph whose nodes read/update a GraphState.
    - add_node: register a function under a name.
    - add_edge: always go from A to B.
    - add_conditional_edges: call a function on the state to pick the next node.
    - compile(): validate the wiring and return a Runnable (.invoke/.stream).
    """
    graph = StateGraph(GraphState)
    graph.add_node("retrieve", retrieve)
    graph.add_node("grade_documents", grade_documents)
    graph.add_node("generate", generate)
    graph.add_node("no_answer", no_answer)

    graph.add_edge(START, "retrieve")
    graph.add_edge("retrieve", "grade_documents")
    graph.add_conditional_edges(
        "grade_documents",
        decide_next_step,
        {"generate": "generate", "no_answer": "no_answer"},
    )
    graph.add_edge("generate", END)
    graph.add_edge("no_answer", END)
    return graph.compile()


@lru_cache
def get_graph() -> CompiledStateGraph:
    """Compile once and reuse."""
    return build_graph()


def ask_graph(question: str) -> GraphResult:
    """Run the graph and report which chunks the grader kept or dropped."""
    settings = get_settings()
    configure_tracing(settings)

    state = get_graph().invoke(
        {"question": question},
        config={"run_name": settings.graph_run_name, "tags": settings.graph_tags},
    )
    kept = state.get("relevant_documents", [])
    filtered_out = [doc for doc in state.get("documents", []) if doc not in kept]
    return GraphResult(answer=state["answer"], kept=kept, filtered_out=filtered_out)

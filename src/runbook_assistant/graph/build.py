"""Build the LangGraph:
route -> retrieve -> grade -> generate | rewrite (loop back to retrieve) | no_answer.
"""

from dataclasses import dataclass
from functools import lru_cache, partial
from typing import Literal, TypedDict

from langchain_core.documents import Document
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from runbook_assistant.config import Settings, configure_tracing, get_settings
from runbook_assistant.graph.nodes import (
    generate,
    grade_documents,
    no_answer,
    retrieve,
    rewrite_query,
    route_question,
)
from runbook_assistant.graph.state import GraphState

NextStep = Literal["generate", "rewrite_query", "no_answer"]


class GraphResult(TypedDict):
    answer: str
    topic: str
    search_queries: list[str]
    retry_count: int
    kept: list[Document]
    filtered_out: list[Document]


def decide_next_step(state: GraphState, max_retries: int) -> NextStep:
    """Routing function for the conditional edge after grading.

    Pure function of the state (no API calls), so it is easy to unit test.
    retry_count only grows, so the rewrite branch can be taken at most
    max_retries times: the loop always terminates.
    """
    if state.get("relevant_documents"):
        return "generate"
    if state.get("retry_count", 0) < max_retries:
        return "rewrite_query"
    return "no_answer"


def recursion_limit(max_retries: int) -> int:
    """LangGraph step budget: a backstop on top of decide_next_step.

    Worst case: route + (retrieve + grade) per attempt + rewrites + final node.
    LangGraph stops when the step count reaches the limit, so allow one extra.
    """
    worst_case_steps = 1 + 2 * (max_retries + 1) + max_retries + 1
    return worst_case_steps + 1


@dataclass(frozen=True)
class GraphOptions:
    """Feature flags for one graph setup (frozen, so it can be a cache key)."""

    routing_enabled: bool = True
    grading_enabled: bool = True
    max_retries: int = 1

    @classmethod
    def from_settings(cls, settings: Settings) -> "GraphOptions":
        return cls(
            routing_enabled=settings.routing_enabled,
            grading_enabled=settings.grading_enabled,
            max_retries=settings.max_retries,
        )


def build_graph(options: GraphOptions) -> CompiledStateGraph:
    """Wire nodes and edges, then compile.

    The node set is the same for every setup; disabled features turn their
    node into a no-op (via functools.partial), so the diagram never changes.

    - StateGraph(GraphState): a graph whose nodes read/update a GraphState.
    - add_node: register a function under a name.
    - add_edge: always go from A to B.
    - add_conditional_edges: call a function on the state to pick the next node.
    - rewrite_query -> retrieve is an edge back to an earlier node: a cycle.
    - compile(): validate the wiring and return a Runnable (.invoke/.stream).
    """
    graph = StateGraph(GraphState)
    graph.add_node(
        "route_question", partial(route_question, enabled=options.routing_enabled)
    )
    graph.add_node("retrieve", retrieve)
    graph.add_node(
        "grade_documents", partial(grade_documents, enabled=options.grading_enabled)
    )
    graph.add_node("rewrite_query", rewrite_query)
    graph.add_node("generate", generate)
    graph.add_node("no_answer", no_answer)

    graph.add_edge(START, "route_question")
    graph.add_edge("route_question", "retrieve")
    graph.add_edge("retrieve", "grade_documents")
    graph.add_conditional_edges(
        "grade_documents",
        partial(decide_next_step, max_retries=options.max_retries),
        {
            "generate": "generate",
            "rewrite_query": "rewrite_query",
            "no_answer": "no_answer",
        },
    )
    graph.add_edge("rewrite_query", "retrieve")  # the loop
    graph.add_edge("generate", END)
    graph.add_edge("no_answer", END)
    return graph.compile()


@lru_cache
def get_graph(options: GraphOptions) -> CompiledStateGraph:
    """Compile once per setup and reuse."""
    return build_graph(options)


def ask_graph(question: str, options: GraphOptions | None = None) -> GraphResult:
    """Run the graph and report routing, queries, and grading decisions.

    `options` defaults to the flags in config; evals pass explicit options.
    """
    settings = get_settings()
    configure_tracing(settings)
    options = options or GraphOptions.from_settings(settings)

    state = get_graph(options).invoke(
        {"question": question},
        config={
            "run_name": settings.graph_run_name,
            "tags": settings.graph_tags,
            "recursion_limit": recursion_limit(options.max_retries),
        },
    )
    return GraphResult(
        answer=state["answer"],
        topic=state["topic"],
        search_queries=state["search_queries"],
        retry_count=state["retry_count"],
        kept=state.get("relevant_documents", []),
        filtered_out=state.get("filtered_documents", []),
    )

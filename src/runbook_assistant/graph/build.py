"""Build the LangGraph:
route -> retrieve -> grade -> generate | rewrite (loop back to retrieve) | no_answer.
"""

from functools import lru_cache, partial
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


def build_graph(max_retries: int) -> CompiledStateGraph:
    """Wire nodes and edges, then compile.

    - StateGraph(GraphState): a graph whose nodes read/update a GraphState.
    - add_node: register a function under a name.
    - add_edge: always go from A to B.
    - add_conditional_edges: call a function on the state to pick the next node.
    - rewrite_query -> retrieve is an edge back to an earlier node: a cycle.
    - compile(): validate the wiring and return a Runnable (.invoke/.stream).
    """
    graph = StateGraph(GraphState)
    graph.add_node("route_question", route_question)
    graph.add_node("retrieve", retrieve)
    graph.add_node("grade_documents", grade_documents)
    graph.add_node("rewrite_query", rewrite_query)
    graph.add_node("generate", generate)
    graph.add_node("no_answer", no_answer)

    graph.add_edge(START, "route_question")
    graph.add_edge("route_question", "retrieve")
    graph.add_edge("retrieve", "grade_documents")
    graph.add_conditional_edges(
        "grade_documents",
        partial(decide_next_step, max_retries=max_retries),
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
def get_graph() -> CompiledStateGraph:
    """Compile once and reuse."""
    return build_graph(get_settings().max_retries)


def ask_graph(question: str) -> GraphResult:
    """Run the graph and report routing, queries, and grading decisions."""
    settings = get_settings()
    configure_tracing(settings)

    state = get_graph().invoke(
        {"question": question},
        config={
            "run_name": settings.graph_run_name,
            "tags": settings.graph_tags,
            "recursion_limit": recursion_limit(settings.max_retries),
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

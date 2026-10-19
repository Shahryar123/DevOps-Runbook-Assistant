"""Build the LangGraph:
input_guardrails -> blocked_reply | route -> off_topic_reply | retrieve -> grade
-> generate -> output_guardrails | rewrite (loop back to retrieve) | no_answer.
"""

import time
from dataclasses import dataclass
from functools import lru_cache, partial
from typing import Literal, TypedDict
from uuid import UUID, uuid4

from langchain_core.documents import Document
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from runbook_assistant.config import Settings, configure_tracing, get_settings
from runbook_assistant.graph.nodes import (
    blocked_reply,
    generate,
    grade_documents,
    input_guardrails,
    no_answer,
    off_topic_reply,
    output_guardrails,
    retrieve,
    rewrite_query,
    route_question,
)
from runbook_assistant.graph.state import GraphState
from runbook_assistant.guardrails.commands import CommandWarning
from runbook_assistant.guardrails.events import GuardrailEvent
from runbook_assistant.guardrails.secrets import mask_with_event
from runbook_assistant.tracing import tracer_callbacks

NextStep = Literal["generate", "rewrite_query", "no_answer"]


class GraphResult(TypedDict):
    answer: str
    topic: str
    search_queries: list[str]
    retry_count: int
    kept: list[Document]
    filtered_out: list[Document]
    run_id: UUID
    latency_s: float
    question_sent: str  # the question after secret masking
    answer_body: str  # answer without the command warnings block
    command_warnings: list[CommandWarning]
    guardrail_events: list[GuardrailEvent]
    blocked_reason: str | None


def after_input_guardrails(
    state: GraphState,
) -> Literal["blocked_reply", "route_question"]:
    return "blocked_reply" if state.get("blocked_reason") else "route_question"


def after_routing(state: GraphState) -> Literal["off_topic_reply", "retrieve"]:
    off_topic = get_settings().off_topic_topic
    return "off_topic_reply" if state.get("topic") == off_topic else "retrieve"


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

    Worst case: input_guardrails + route + (retrieve + grade) per attempt
    + rewrites + generate + output_guardrails.
    LangGraph stops when the step count reaches the limit, so allow one extra.
    """
    worst_case_steps = 2 + 2 * (max_retries + 1) + max_retries + 2
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
    graph.add_node("input_guardrails", input_guardrails)
    graph.add_node("blocked_reply", blocked_reply)
    graph.add_node(
        "route_question", partial(route_question, enabled=options.routing_enabled)
    )
    graph.add_node("off_topic_reply", off_topic_reply)
    graph.add_node("retrieve", retrieve)
    graph.add_node(
        "grade_documents", partial(grade_documents, enabled=options.grading_enabled)
    )
    graph.add_node("rewrite_query", rewrite_query)
    graph.add_node("generate", generate)
    graph.add_node("no_answer", no_answer)
    graph.add_node("output_guardrails", output_guardrails)

    graph.add_edge(START, "input_guardrails")
    graph.add_conditional_edges(
        "input_guardrails",
        after_input_guardrails,
        {"blocked_reply": "blocked_reply", "route_question": "route_question"},
    )
    graph.add_conditional_edges(
        "route_question",
        after_routing,
        {"off_topic_reply": "off_topic_reply", "retrieve": "retrieve"},
    )
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
    graph.add_edge("generate", "output_guardrails")
    graph.add_edge("output_guardrails", END)
    graph.add_edge("no_answer", END)
    graph.add_edge("blocked_reply", END)
    graph.add_edge("off_topic_reply", END)
    return graph.compile()


@lru_cache
def get_graph(options: GraphOptions) -> CompiledStateGraph:
    """Compile once per setup and reuse."""
    return build_graph(options)


def ask_graph(
    question: str,
    options: GraphOptions | None = None,
    run_id: UUID | None = None,
    tags: list[str] | None = None,
) -> GraphResult:
    """Run the graph and report routing, queries, and grading decisions.

    `options` defaults to the flags in config; evals pass explicit options.
    `run_id` becomes the ID of the root run in LangSmith (a new one is made
    if not given), so callers can link to the trace or attach feedback.
    `tags` are added to the configured graph tags.

    Secrets in the question are masked HERE, before invoke: the graph, every
    model call and the LangSmith trace only ever see the masked question.
    """
    settings = get_settings()
    configure_tracing(settings)
    options = options or GraphOptions.from_settings(settings)
    run_id = run_id or uuid4()
    masked, events = mask_with_event(question, settings, "question")

    start = time.perf_counter()
    state = get_graph(options).invoke(
        {"question": masked.text, "guardrail_events": events},
        config={
            "run_id": run_id,
            "run_name": settings.graph_run_name,
            "tags": [*settings.graph_tags, *(tags or [])],
            "recursion_limit": recursion_limit(options.max_retries),
            "callbacks": tracer_callbacks(),
        },
    )
    latency_s = time.perf_counter() - start
    return GraphResult(
        answer=state["answer"],
        # Blocked questions stop before routing/retrieval, so these may be unset.
        topic=state.get("topic", ""),
        search_queries=state.get("search_queries", []),
        retry_count=state.get("retry_count", 0),
        kept=state.get("relevant_documents", []),
        filtered_out=state.get("filtered_documents", []),
        run_id=run_id,
        latency_s=latency_s,
        question_sent=masked.text,
        answer_body=state.get("answer_body", state["answer"]),
        command_warnings=state.get("command_warnings", []),
        guardrail_events=state.get("guardrail_events", []),
        blocked_reason=state.get("blocked_reason"),
    )

"""Graph nodes: input_guardrails, blocked_reply, route_question,
off_topic_reply, retrieve, grade_documents, rewrite_query, generate,
no_answer, output_guardrails.

A node is a plain function: (state) -> partial state update (dict).
"""

from collections.abc import Sequence
from functools import lru_cache
from typing import Literal

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable
from pydantic import BaseModel, Field, create_model

from runbook_assistant.config import get_settings
from runbook_assistant.graph.state import GraphState
from runbook_assistant.guardrails.commands import (
    find_dangerous_commands,
    format_warnings,
)
from runbook_assistant.guardrails.events import guardrail_event
from runbook_assistant.guardrails.input import run_input_guardrails
from runbook_assistant.guardrails.replies import BLOCKED_REPLIES, OFF_TOPIC_REPLY
from runbook_assistant.guardrails.secrets import mask_with_event
from runbook_assistant.llm import get_chat_model
from runbook_assistant.rag import NO_ANSWER, PROMPT, format_docs
from runbook_assistant.retriever import get_retriever

# --- Router -----------------------------------------------------------------

ROUTER_SYSTEM_PROMPT = """You route DevOps troubleshooting questions to the runbook topic
most likely to contain the answer.

Topics:
{topics}
- {general_topic}: an IT/DevOps question that does not clearly fit one topic above,
  or spans several.{off_topic}

Pick exactly one topic."""

OFF_TOPIC_LINE = """
- {name}: the question is not about IT, servers, software or DevOps at all
  (e.g. food, sports, travel, personal advice). Any IT question, even one no
  runbook covers (e.g. Kubernetes, Oracle), is NOT {name}."""

ROUTER_PROMPT = ChatPromptTemplate.from_messages(
    [("system", ROUTER_SYSTEM_PROMPT), ("human", "{question}")]
)


def make_route_model(topics: Sequence[str]) -> type[BaseModel]:
    """Build the router's output schema from the configured topics.

    The field is a Literal of exactly these values, so structured output can
    only produce a valid topic, and Pydantic rejects anything else.
    """
    return create_model(
        "RouteQuestion",
        __doc__="The topic a DevOps question belongs to.",
        topic=(
            Literal[tuple(topics)],
            Field(description="The single best-matching topic for the question"),
        ),
    )


@lru_cache
def get_router() -> Runnable:
    settings = get_settings()
    topics = "\n".join(
        f"- {name}: {description}"
        for name, description in settings.runbook_topics.items()
    )
    llm = get_chat_model(settings.router_model)
    off_topic = (
        OFF_TOPIC_LINE.format(name=settings.off_topic_topic)
        if settings.off_topic_enabled
        else ""
    )
    prompt = ROUTER_PROMPT.partial(
        topics=topics, general_topic=settings.general_topic, off_topic=off_topic
    )
    return prompt | llm.with_structured_output(make_route_model(settings.router_topics))


# --- Grader -----------------------------------------------------------------

GRADER_SYSTEM_PROMPT = """You grade whether a runbook excerpt is relevant to a DevOps question.
Answer "yes" if the excerpt contains information that would help answer the question
(symptoms, causes, diagnosis steps, commands, or fixes for the same problem).
Answer "no" if it is about a different system or problem.
Be lenient: partially relevant excerpts count as "yes"."""

GRADER_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", GRADER_SYSTEM_PROMPT),
        ("human", "Question: {question}\n\nRunbook excerpt:\n{document}"),
    ]
)


class GradeDocument(BaseModel):
    """Relevance verdict for one retrieved chunk."""

    binary_score: Literal["yes", "no"] = Field(
        description="'yes' if the excerpt is relevant to the question, otherwise 'no'"
    )


@lru_cache
def get_grader() -> Runnable:
    """Prompt -> LLM that must reply with a GradeDocument.

    with_structured_output() makes the model return data matching the
    Pydantic schema (via OpenAI's structured outputs), so we get a typed
    object with binary_score == "yes" | "no" instead of free text to parse.
    """
    llm = get_chat_model(get_settings().grader_model)
    return GRADER_PROMPT | llm.with_structured_output(GradeDocument)


# --- Rewriter ---------------------------------------------------------------

REWRITE_SYSTEM_PROMPT = """You rewrite DevOps troubleshooting questions into search queries
for a runbook search engine. The previous search found nothing relevant.

Use precise technical terms: product and component names, error codes, event IDs,
HTTP status codes and concrete symptoms.
Example: "website dead after new build" -> "IIS application pool stopped 503 after deployment"

Return only the search query, with no explanation or quotes."""

REWRITE_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", REWRITE_SYSTEM_PROMPT),
        ("human", "Question: {question}\nPrevious search query: {search_query}"),
    ]
)


@lru_cache
def get_rewriter() -> Runnable:
    llm = get_chat_model(get_settings().rewrite_model)
    return REWRITE_PROMPT | llm | StrOutputParser()


# --- Generator --------------------------------------------------------------


@lru_cache
def get_generator() -> Runnable:
    """Same prompt as the Milestone 2 chain, so both answer by the same rules."""
    llm = get_chat_model(get_settings().answer_model)
    return PROMPT | llm | StrOutputParser()


# --- Nodes ------------------------------------------------------------------


def input_guardrails(state: GraphState) -> GraphState:
    """Length -> moderation -> injection checks on the (already masked) question."""
    check = run_input_guardrails(state["question"], get_settings())
    return {"blocked_reason": check.blocked_reason, "guardrail_events": check.events}


def blocked_reply(state: GraphState) -> GraphState:
    """Fixed reply for a blocked question: no retrieval, no generation."""
    return {"answer": BLOCKED_REPLIES[state["blocked_reason"]]}


def off_topic_reply(state: GraphState) -> GraphState:
    """Fixed reply when the router says the question isn't about IT at all."""
    event = guardrail_event("off_topic", "blocked", "router chose off_topic")
    return {"answer": OFF_TOPIC_REPLY, "guardrail_events": [event]}


def route_question(state: GraphState, enabled: bool = True) -> GraphState:
    """Classify the question into a topic and initialise the search state.

    With routing disabled, no LLM call is made and the topic is the general
    fallback, so retrieval searches every topic.
    """
    question = state["question"]
    if enabled:
        topic = get_router().invoke({"question": question}).topic
    else:
        topic = get_settings().general_topic
    return {
        "topic": topic,
        "search_query": question,
        "search_queries": [question],
        "retry_count": 0,
    }


def topic_filter(
    topic: str | None, retry_count: int, general_topic: str
) -> dict[str, str] | None:
    """Chroma metadata filter for this attempt.

    First attempt: only the routed topic (unless it is the general fallback).
    Retries: no filter, as a safety net in case the router picked wrong.
    """
    if retry_count > 0 or not topic or topic == general_topic:
        return None
    return {"topic": topic}


def retrieve(state: GraphState) -> GraphState:
    """Fetch the top-k chunks for the current search query."""
    metadata_filter = topic_filter(
        state.get("topic"), state.get("retry_count", 0), get_settings().general_topic
    )
    retriever = get_retriever(metadata_filter=metadata_filter)
    return {"documents": retriever.invoke(state["search_query"])}


def grade_documents(state: GraphState, enabled: bool = True) -> GraphState:
    """Grade every chunk against the ORIGINAL question in one .batch() call.

    With grading disabled, every retrieved chunk is kept and no LLM call is made.
    """
    documents = state["documents"]
    if not enabled:
        return {"relevant_documents": documents, "filtered_documents": []}
    grades = get_grader().batch(
        [{"question": state["question"], "document": d.page_content} for d in documents]
    )
    relevant, filtered = [], []
    for doc, grade in zip(documents, grades, strict=True):
        (relevant if grade.binary_score == "yes" else filtered).append(doc)
    return {"relevant_documents": relevant, "filtered_documents": filtered}


def rewrite_query(state: GraphState) -> GraphState:
    """Turn the original question into a sharper search query and count the retry."""
    query = get_rewriter().invoke(
        {"question": state["question"], "search_query": state["search_query"]}
    )
    query = query.strip().strip('"')
    return {
        "search_query": query,
        "search_queries": [query],
        "retry_count": state.get("retry_count", 0) + 1,
    }


def generate(state: GraphState) -> GraphState:
    """Answer the ORIGINAL question using only the chunks the grader kept."""
    answer = get_generator().invoke(
        {
            "question": state["question"],
            "context": format_docs(state["relevant_documents"]),
        }
    )
    return {"answer": answer}


def no_answer(state: GraphState) -> GraphState:
    """Fixed reply with no LLM call: costs nothing and cannot hallucinate."""
    return {"answer": NO_ANSWER}


def output_guardrails(state: GraphState) -> GraphState:
    """Mask any secret in the generated answer, then warn about risky commands.

    Masking runs first so a warning can never quote a secret. The warnings are
    appended to `answer`; `answer_body` keeps the answer without them.
    """
    settings = get_settings()
    masked, events = mask_with_event(state["answer"], settings, "answer")
    warnings = (
        find_dangerous_commands(masked.text, settings.dangerous_commands)
        if settings.command_warnings_enabled
        else []
    )
    if warnings:
        names = ", ".join(w.name for w in warnings)
        events.append(guardrail_event("commands", "warned", names))
    return {
        "answer": masked.text + format_warnings(warnings),
        "answer_body": masked.text,
        "command_warnings": warnings,
        "guardrail_events": events,
    }

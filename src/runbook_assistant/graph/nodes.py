"""Graph nodes: route_question, retrieve, grade_documents, rewrite_query,
generate, no_answer.

A node is a plain function: (state) -> partial state update (dict).
"""

from collections.abc import Sequence
from functools import lru_cache
from typing import Literal

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field, create_model

from runbook_assistant.config import get_settings
from runbook_assistant.graph.state import GraphState
from runbook_assistant.rag import NO_ANSWER, PROMPT, format_docs
from runbook_assistant.retriever import get_retriever

# --- Router -----------------------------------------------------------------

ROUTER_SYSTEM_PROMPT = """You route DevOps troubleshooting questions to the runbook topic
most likely to contain the answer.

Topics:
{topics}
- {general_topic}: the question does not clearly fit one topic above, spans several,
  or is not about DevOps.

Pick exactly one topic."""

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
    llm = ChatOpenAI(model=settings.router_model, api_key=settings.openai_api_key)
    prompt = ROUTER_PROMPT.partial(topics=topics, general_topic=settings.general_topic)
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
    settings = get_settings()
    llm = ChatOpenAI(model=settings.grader_model, api_key=settings.openai_api_key)
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
    settings = get_settings()
    llm = ChatOpenAI(model=settings.rewrite_model, api_key=settings.openai_api_key)
    return REWRITE_PROMPT | llm | StrOutputParser()


# --- Generator --------------------------------------------------------------


@lru_cache
def get_generator() -> Runnable:
    """Same prompt as the Milestone 2 chain, so both answer by the same rules."""
    settings = get_settings()
    llm = ChatOpenAI(model=settings.answer_model, api_key=settings.openai_api_key)
    return PROMPT | llm | StrOutputParser()


# --- Nodes ------------------------------------------------------------------


def route_question(state: GraphState) -> GraphState:
    """Classify the question into a topic and initialise the search state."""
    question = state["question"]
    route = get_router().invoke({"question": question})
    return {
        "topic": route.topic,
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


def grade_documents(state: GraphState) -> GraphState:
    """Grade every chunk against the ORIGINAL question in one .batch() call."""
    documents = state["documents"]
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

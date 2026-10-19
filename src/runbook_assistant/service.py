"""What the UI needs, as plain Python (no Streamlit): answer a question with
a chosen setup, link to its trace, send feedback, and check the index.

Keeping this separate from app.py means it can be unit tested, and any other
frontend (a CLI, a FastAPI + React app) could reuse it unchanged.
"""

import logging
from functools import lru_cache
from typing import Literal, TypedDict
from uuid import UUID

import openai
from langchain_core.documents import Document
from langchain_core.runnables import Runnable
from langsmith import Client

from runbook_assistant.config import Settings, get_settings
from runbook_assistant.graph.build import GraphOptions, ask_graph, get_graph
from runbook_assistant.rag import ask_with_sources, get_chain, is_refusal
from runbook_assistant.retriever import RunbookInfo, list_runbooks

logger = logging.getLogger(__name__)

# The system versions that can answer a question (shared with evals/run_evals.py).
SETUPS: tuple[str, ...] = ("chain", "graph", "graph_routing")
NO_ROUTING_TOPIC = "n/a (no routing)"

IndexStatus = Literal["missing", "empty", "ok"]


class AnswerResult(TypedDict):
    """One answer plus everything the UI shows about how it was produced."""

    question: str
    setup: str
    answer: str
    is_refusal: bool
    topic: str
    search_queries: list[str]
    retry_count: int
    kept: list[Document]
    filtered_out: list[Document]
    run_id: str
    latency_s: float
    trace_url: str | None


def options_for_setup(setup: str, settings: Settings) -> GraphOptions | None:
    """None for the chain; otherwise routing from the setup, the rest from config."""
    if setup not in SETUPS:
        raise ValueError(f"unknown setup {setup!r}; expected one of {SETUPS}")
    if setup == "chain":
        return None
    return GraphOptions(
        routing_enabled=setup == "graph_routing",
        grading_enabled=settings.grading_enabled,
        max_retries=settings.max_retries,
    )


def answer(question: str, setup: str) -> AnswerResult:
    """Answer with the chosen setup and map the result to one common shape."""
    settings = get_settings()
    options = options_for_setup(setup, settings)

    if options is None:
        rag = ask_with_sources(question, tags=settings.ui_tags)
        return AnswerResult(
            question=question,
            setup=setup,
            answer=rag["answer"],
            is_refusal=is_refusal(rag["answer"]),
            topic=NO_ROUTING_TOPIC,
            search_queries=[question],
            retry_count=0,
            kept=rag["docs"],
            filtered_out=[],
            run_id=str(rag["run_id"]),
            latency_s=rag["latency_s"],
            trace_url=trace_url(str(rag["run_id"])),
        )

    graph = ask_graph(question, options, tags=settings.ui_tags)
    return AnswerResult(
        question=question,
        setup=setup,
        answer=graph["answer"],
        is_refusal=is_refusal(graph["answer"]),
        topic=graph["topic"],
        search_queries=graph["search_queries"],
        retry_count=graph["retry_count"],
        kept=graph["kept"],
        filtered_out=graph["filtered_out"],
        run_id=str(graph["run_id"]),
        latency_s=graph["latency_s"],
        trace_url=trace_url(str(graph["run_id"])),
    )


# --- LangSmith: trace links and feedback ---------------------------------------


def tracing_enabled(settings: Settings) -> bool:
    return settings.langsmith_tracing and settings.langsmith_api_key is not None


@lru_cache
def get_langsmith_client() -> Client:
    settings = get_settings()
    assert settings.langsmith_api_key is not None
    return Client(api_key=settings.langsmith_api_key.get_secret_value())


@lru_cache
def project_path(project_name: str) -> str:
    """ "o/<org id>/projects/p/<project id>", looked up once per project.

    lru_cache does not cache exceptions, so a failed lookup is retried next time.
    """
    project = get_langsmith_client().read_project(project_name=project_name)
    return f"o/{project.tenant_id}/projects/p/{project.id}"


def trace_url(run_id: str) -> str | None:
    """Link to the run in the LangSmith UI, or None if tracing is off or the
    project can't be looked up. `poll=true` makes the page wait until the
    run has finished uploading."""
    settings = get_settings()
    if not tracing_enabled(settings):
        return None
    try:
        path = project_path(settings.langsmith_project)
    except Exception:
        logger.warning("Could not look up the LangSmith project", exc_info=True)
        return None
    return f"{settings.langsmith_web_url.rstrip('/')}/{path}/r/{run_id}?poll=true"


def send_feedback(run_id: str, score: int, comment: str = "") -> None:
    """Attach a user rating (1 = thumbs up, 0 = thumbs down) to the run.

    The run is the root of its trace, so trace_id = run_id; giving it lets
    LangSmith accept feedback even if the run is still uploading.
    """
    settings = get_settings()
    get_langsmith_client().create_feedback(
        run_id=UUID(run_id),
        trace_id=UUID(run_id),
        key=settings.feedback_key,
        score=score,
        comment=comment.strip() or None,
    )


# --- Startup checks and errors ---------------------------------------------------


def runbook_index(settings: Settings) -> tuple[IndexStatus, list[RunbookInfo]]:
    """Has ingest been run, and which runbooks are in the store?

    Checks the folder before opening Chroma, because opening a missing folder
    would silently create an empty database.
    """
    if not settings.chroma_dir.exists():
        return "missing", []
    runbooks = list_runbooks()
    return ("ok" if runbooks else "empty"), runbooks


def load_setup(setup: str) -> Runnable:
    """Build (or fetch the already-built) chain or compiled graph for a setup.

    Also opens the vector store, since the chain/graph uses it. Called at
    startup so the first question doesn't pay the setup cost.
    """
    options = options_for_setup(setup, get_settings())
    return get_chain() if options is None else get_graph(options)


def friendly_error(exc: Exception) -> str:
    """A short message for the user. Callers log the full error separately."""
    # APITimeoutError is a subclass of APIConnectionError, so check it first.
    if isinstance(exc, openai.RateLimitError):
        return (
            "OpenAI is rate limiting requests right now. Wait a moment and try again."
        )
    if isinstance(exc, openai.APITimeoutError):
        return "OpenAI took too long to respond. Please try again."
    if isinstance(exc, openai.APIConnectionError):
        return "Couldn't reach OpenAI. Check your internet connection and try again."
    if isinstance(exc, openai.AuthenticationError):
        return "OpenAI rejected the API key. Check OPENAI_API_KEY in your .env file."
    if isinstance(exc, openai.APIError):
        return "OpenAI returned an error. Please try again in a moment."
    return "Something went wrong while answering. The details are in the app log."

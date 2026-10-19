"""Basic RAG: retriever -> prompt -> ChatOpenAI -> string, built with LCEL."""

import re
import time
from functools import lru_cache
from typing import TypedDict
from uuid import UUID, uuid4

from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable, RunnableParallel, RunnablePassthrough
from langchain_openai import ChatOpenAI

from runbook_assistant.config import configure_tracing, get_settings
from runbook_assistant.retriever import get_retriever

NO_ANSWER = "I don't have a runbook for this"

SYSTEM_PROMPT = f"""You are a DevOps troubleshooting assistant.
Answer the user's question using ONLY the runbook excerpts provided in the context.

Rules:
- Use only facts from the context. Do not use outside knowledge or guess.
- Cite the source runbook filename(s) you used, e.g. (source: aws-ec2-unreachable.md).
- If the context does not contain the answer, reply exactly: "{NO_ANSWER}" and nothing else.
- Be concise. Use numbered steps and include commands from the runbook when relevant."""

PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", SYSTEM_PROMPT),
        ("human", "Context:\n{context}\n\nQuestion: {question}"),
    ]
)


class RagResult(TypedDict):
    answer: str
    docs: list[Document]
    run_id: UUID
    latency_s: float


def normalize(text: str) -> str:
    """Lowercase, unify apostrophes, drop punctuation, collapse whitespace."""
    text = text.lower().replace("’", "'")
    text = re.sub(r"[^a-z0-9' ]+", " ", text)
    return " ".join(text.split())


def is_refusal(answer: str) -> bool:
    """True if the answer is the fixed "I don't have a runbook for this" reply."""
    return normalize(answer).startswith(normalize(NO_ANSWER))


def format_docs(docs: list[Document]) -> str:
    """Render retrieved chunks as labelled text so the model can cite sources."""
    return "\n\n---\n\n".join(
        f"[source: {doc.metadata['source']} | section: {doc.metadata['section']}]\n"
        f"{doc.page_content}"
        for doc in docs
    )


@lru_cache
def get_chain() -> Runnable:
    """Build the RAG chain once.

    LCEL (LangChain Expression Language): `a | b` makes a new Runnable that
    feeds a's output into b. The input is the question string.

    1. RunnableParallel runs the retriever and a passthrough on the same input
       -> {"docs": [...], "question": "..."}
    2. RunnablePassthrough.assign adds new keys while keeping existing ones:
       first "context" (formatted docs), then "answer" (prompt | llm | parser).
    """
    settings = get_settings()
    configure_tracing(settings)

    llm = ChatOpenAI(model=settings.answer_model, api_key=settings.openai_api_key)
    generate = (
        RunnablePassthrough.assign(context=lambda x: format_docs(x["docs"]))
        | PROMPT
        | llm
        | StrOutputParser()  # AIMessage -> plain str
    )
    retrieve = RunnableParallel(docs=get_retriever(), question=RunnablePassthrough())
    return retrieve | RunnablePassthrough.assign(answer=generate)


def ask_with_sources(
    question: str, run_id: UUID | None = None, tags: list[str] | None = None
) -> RagResult:
    """Answer a question and also return the chunks retrieval found.

    `run_id` becomes the ID of the root run in LangSmith (a new one is made
    if not given), so callers can link to the trace or attach feedback.
    """
    settings = get_settings()
    run_id = run_id or uuid4()
    start = time.perf_counter()
    result = get_chain().invoke(
        question,
        config={
            "run_id": run_id,
            "run_name": settings.chain_run_name,
            "tags": tags or [],
        },
    )
    return RagResult(
        answer=result["answer"],
        docs=result["docs"],
        run_id=run_id,
        latency_s=time.perf_counter() - start,
    )


def ask(question: str) -> str:
    """Answer a question from the runbooks."""
    return ask_with_sources(question)["answer"]

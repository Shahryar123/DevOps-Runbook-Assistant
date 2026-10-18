"""Basic RAG: retriever -> prompt -> ChatOpenAI -> string, built with LCEL."""

from functools import lru_cache
from typing import TypedDict

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


def ask_with_sources(question: str) -> RagResult:
    """Answer a question and also return the chunks retrieval found."""
    result = get_chain().invoke(question)
    return RagResult(answer=result["answer"], docs=result["docs"])


def ask(question: str) -> str:
    """Answer a question from the runbooks."""
    return ask_with_sources(question)["answer"]

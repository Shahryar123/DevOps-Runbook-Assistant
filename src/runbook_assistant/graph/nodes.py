"""Graph nodes: retrieve, grade_documents, generate, no_answer.

A node is a plain function: (state) -> partial state update (dict).
"""

from functools import lru_cache
from typing import Literal

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from runbook_assistant.config import get_settings
from runbook_assistant.graph.state import GraphState
from runbook_assistant.rag import NO_ANSWER, PROMPT, format_docs
from runbook_assistant.retriever import get_retriever

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


@lru_cache
def get_generator() -> Runnable:
    """Same prompt as the Milestone 2 chain, so both answer by the same rules."""
    settings = get_settings()
    llm = ChatOpenAI(model=settings.answer_model, api_key=settings.openai_api_key)
    return PROMPT | llm | StrOutputParser()


def retrieve(state: GraphState) -> GraphState:
    """Fetch the top-k chunks for the question."""
    return {"documents": get_retriever().invoke(state["question"])}


def grade_documents(state: GraphState) -> GraphState:
    """Ask the grader about every chunk in one parallel .batch() call."""
    documents = state["documents"]
    grades = get_grader().batch(
        [{"question": state["question"], "document": d.page_content} for d in documents]
    )
    relevant = [
        doc
        for doc, grade in zip(documents, grades, strict=True)
        if grade.binary_score == "yes"
    ]
    return {"relevant_documents": relevant}


def generate(state: GraphState) -> GraphState:
    """Answer using only the chunks the grader kept."""
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

"""The shared state that flows through the LangGraph."""

from typing import TypedDict

from langchain_core.documents import Document


class GraphState(TypedDict, total=False):
    """State passed between nodes.

    Each node receives the full state and returns a dict with only the keys
    it changed; LangGraph merges that update into the state.
    total=False means a key may be absent until a node sets it.
    """

    question: str
    documents: list[Document]  # everything the retriever returned
    relevant_documents: list[Document]  # chunks the grader marked relevant
    answer: str

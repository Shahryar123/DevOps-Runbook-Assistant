"""The shared state that flows through the LangGraph."""

import operator
from typing import Annotated, TypedDict

from langchain_core.documents import Document


class GraphState(TypedDict, total=False):
    """State passed between nodes.

    Each node receives the full state and returns a dict with only the keys
    it changed; LangGraph merges that update into the state.
    total=False means a key may be absent until a node sets it.
    """

    question: str  # the user's original question, never modified
    topic: str  # set by route_question
    search_query: str  # what retrieve actually searches with
    # Annotated[..., operator.add] is a *reducer*: updates are appended to the
    # existing list instead of replacing it, giving a history of all queries.
    search_queries: Annotated[list[str], operator.add]
    retry_count: int  # number of rewrites done so far
    documents: list[Document]  # everything the latest retrieve returned
    relevant_documents: list[Document]  # chunks the grader marked relevant
    # Every chunk the grader rejected, across all attempts (also a reducer).
    filtered_documents: Annotated[list[Document], operator.add]
    answer: str

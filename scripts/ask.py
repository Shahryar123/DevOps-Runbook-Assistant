"""Ask the runbook assistant a question from the command line.

Run: uv run python scripts/ask.py "my question"               (graph, default)
     uv run python scripts/ask.py --mode chain "my question"  (Milestone 2 chain)
"""

import argparse

from langchain_core.documents import Document

from runbook_assistant.graph.build import ask_graph
from runbook_assistant.rag import ask_with_sources

PREVIEW_CHARS = 100


def print_chunks(heading: str, docs: list[Document]) -> None:
    print(f"\n--- {heading} ({len(docs)}) ---")
    for i, doc in enumerate(docs, start=1):
        preview = " ".join(doc.page_content.split())[:PREVIEW_CHARS]
        meta = doc.metadata
        print(f"{i}. {meta['source']} | {meta['topic']} | {meta['section']}")
        print(f"   {preview}")


def run_chain(question: str) -> None:
    result = ask_with_sources(question)
    print(result["answer"])
    print_chunks("Retrieved chunks", result["docs"])


def run_graph(question: str) -> None:
    result = ask_graph(question)

    print(f"Routed topic: {result['topic']}")
    print("Search queries:")
    for i, query in enumerate(result["search_queries"]):
        label = "original" if i == 0 else f"rewrite {i}"
        print(f"  {i + 1}. [{label}] {query}")
    print(f"Retries: {result['retry_count']}")

    print_chunks("KEPT by grader", result["kept"])
    print_chunks("FILTERED OUT by grader (all attempts)", result["filtered_out"])

    print("\n--- Answer ---")
    print(result["answer"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Ask the runbook assistant.")
    parser.add_argument("question", help="Your troubleshooting question")
    parser.add_argument(
        "--mode",
        choices=["chain", "graph"],
        default="graph",
        help="graph = LangGraph with routing, grading and rewrite; "
        "chain = basic RAG baseline",
    )
    args = parser.parse_args()

    if args.mode == "chain":
        run_chain(args.question)
    else:
        run_graph(args.question)


if __name__ == "__main__":
    main()

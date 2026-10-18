"""Ask the runbook assistant a question from the command line.

Run: uv run python scripts/ask.py "my question"
"""

import argparse

from runbook_assistant.rag import ask_with_sources

PREVIEW_CHARS = 100


def main() -> None:
    parser = argparse.ArgumentParser(description="Ask the runbook assistant.")
    parser.add_argument("question", help="Your troubleshooting question")
    args = parser.parse_args()

    result = ask_with_sources(args.question)
    print(result["answer"])

    print("\n--- Retrieved chunks ---")
    for i, doc in enumerate(result["docs"], start=1):
        preview = " ".join(doc.page_content.split())[:PREVIEW_CHARS]
        print(f"{i}. {doc.metadata['source']} | {doc.metadata['section']}")
        print(f"   {preview}")


if __name__ == "__main__":
    main()

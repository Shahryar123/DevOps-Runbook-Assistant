"""Verify that the OpenAI (and optionally LangSmith) keys in .env work.

Run: uv run python scripts/check_setup.py
"""

import sys

from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from runbook_assistant.config import Settings, configure_tracing, get_settings


def check_chat(settings: Settings) -> bool:
    """ChatOpenAI wraps OpenAI chat models behind LangChain's chat-model interface.

    Every LangChain component is a "Runnable": you call it with .invoke(input).
    For chat models the result is an AIMessage whose text is in .content.
    """
    try:
        llm = ChatOpenAI(model=settings.answer_model, api_key=settings.openai_api_key)
        reply = llm.invoke("Reply with exactly: OK")
        print(
            f"[PASS] chat ({settings.answer_model}): {str(reply.content).strip()[:50]}"
        )
        return True
    except Exception as exc:  # noqa: BLE001 - report any failure
        print(f"[FAIL] chat ({settings.answer_model}): {type(exc).__name__}: {exc}")
        return False


def check_embeddings(settings: Settings) -> bool:
    """OpenAIEmbeddings turns text into vectors; RAG uses these for similarity search."""
    try:
        embeddings = OpenAIEmbeddings(
            model=settings.embedding_model, api_key=settings.openai_api_key
        )
        vector = embeddings.embed_query("ping")
        print(
            f"[PASS] embeddings ({settings.embedding_model}): dimension={len(vector)}"
        )
        return True
    except Exception as exc:  # noqa: BLE001 - report any failure
        print(
            f"[FAIL] embeddings ({settings.embedding_model}): {type(exc).__name__}: {exc}"
        )
        return False


def main() -> int:
    settings = get_settings()
    configure_tracing(settings)
    print(f"LangSmith tracing: {'on' if settings.langsmith_tracing else 'off'}")
    results = [check_chat(settings), check_embeddings(settings)]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())

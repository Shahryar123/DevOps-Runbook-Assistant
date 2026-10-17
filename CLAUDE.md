# Runbook Assistant

AI assistant that answers DevOps troubleshooting questions (IIS, Windows Server,
AWS, Azure, CI/CD) using RAG over runbooks in data/runbooks/.

## Stack

- Python 3.12, managed with uv (never use pip directly)
- LangChain (ChatOpenAI, OpenAIEmbeddings) — never call the openai SDK directly
- LangGraph for the agent flow, Chroma as local vector store
- LangSmith for tracing and evaluation
- Streamlit UI
- Models: gpt-5.4-mini for answers, gpt-5.4-nano for routing/grading

## Commands

- Run: `uv run streamlit run src/runbook_assistant/app.py`
- Ingest: `uv run python -m runbook_assistant.ingest`
- Test: `uv run pytest`
- Lint: `uv run ruff check . --fix && uv run ruff format .`

## Structure

- src/runbook_assistant/config.py — settings loaded via pydantic-settings
- src/runbook_assistant/ingest.py — load, chunk, embed runbooks
- src/runbook_assistant/graph/ — LangGraph state, nodes, graph builder
- evals/ — LangSmith datasets and evaluators
- data/runbooks/ — markdown runbooks (source documents)

## Rules

- I am learning LangChain/LangGraph: explain new concepts briefly when you add them
- Work in small steps; one milestone at a time; propose a plan before large changes
- All config via config.py — no hardcoded keys, model names, or paths
- Type hints everywhere; keep functions small
- Never read or print .env contents

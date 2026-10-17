# Runbook Assistant

An AI assistant that answers DevOps troubleshooting questions (IIS, Windows Server, AWS, Azure, CI/CD). It uses RAG over the markdown runbooks in `data/runbooks/`.

**Stack:** Python, uv, LangChain, LangGraph, Chroma, LangSmith, Streamlit, and OpenAI models through `langchain-openai`.

## Quick start

```powershell
cp .env.example .env        # then fill in OPENAI_API_KEY (and LangSmith keys if you want tracing)
uv sync
uv run python scripts/check_setup.py
```

## Commands

| Task | Command |
|---|---|
| Check keys | `uv run python scripts/check_setup.py` |
| Ingest | `uv run python -m runbook_assistant.ingest` |
| Run UI | `uv run streamlit run src/runbook_assistant/app.py` |
| Test | `uv run pytest` |
| Lint | `uv run ruff check . --fix && uv run ruff format .` |

## Milestones

Each milestone has its own document in [docs/milestones/](docs/milestones/). It explains what changed, how the new code works, and the new LangChain/LangGraph concepts it introduces.

| # | Milestone | Status |
|---|---|---|
| 1 | [Project skeleton](docs/milestones/01-project-skeleton.md) | Done |
| 2 | Ingest (load, chunk, embed into Chroma) | Next |

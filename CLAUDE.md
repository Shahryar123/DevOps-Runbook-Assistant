# Runbook Assistant

AI assistant that answers DevOps troubleshooting questions (IIS, Windows Server,
AWS, Azure, CI/CD) using RAG over runbooks in data/runbooks/.

## Stack

- Python 3.12, managed with uv (never use pip directly)
- LangChain (ChatOpenAI, OpenAIEmbeddings) — never call the openai SDK directly
  (one exception: guardrails/moderation.py, since LangChain has no plain moderation API)
- LangGraph for the agent flow, Chroma as local vector store
- LangSmith for tracing and evaluation
- Streamlit UI
- Models: gpt-5.4-mini for everything (answers, routing, grading)

## Commands

- Run: `uv run streamlit run src/runbook_assistant/app.py`
- Ingest (incremental): `uv run python -m runbook_assistant.ingest`
  - `--dry-run` show the plan, no changes, no embedding calls
  - `--validate-only` validate runbooks only, exit 0/1 (for CI)
  - `--full` force a full rebuild
  - Stop the Streamlit app before ingesting, then restart it (local Chroma is single-process)
- Test: `uv run pytest`
- Lint: `uv run ruff check . --fix && uv run ruff format .`

## Structure

- src/runbook_assistant/config.py — settings loaded via pydantic-settings
- src/runbook_assistant/ingest.py — ingest CLI (flags, exit codes)
- src/runbook_assistant/pipeline/ — sources, chunking, validation, manifest, sync, report, lock
- src/runbook_assistant/graph/ — LangGraph state, nodes, graph builder
- src/runbook_assistant/service.py — UI-facing logic (answer, trace link, feedback)
- src/runbook_assistant/app.py — Streamlit chat UI (UI only; calls service.py)
- src/runbook_assistant/guardrails/ — one module per guardrail (secrets, commands,
  injection, moderation, rate_limit, input); patterns and flags live in config.py
- evals/ — LangSmith datasets and evaluators
- data/runbooks/ — markdown runbooks (source documents)

## Rules

- I am learning LangChain/LangGraph: explain new concepts briefly when you add them
- Work in small steps; one milestone at a time; propose a plan before large changes
- All config via config.py — no hardcoded keys, model names, or paths
- Type hints everywhere; keep functions small
- Never read or print .env contents
- After each milestone, add docs/milestones/NN-<slug>.md covering: what changed (files),
  how it works, new concepts, how to run/verify, notes/decisions, next milestone.
  Then add a row to the Milestones table in README.md. Small changes within a
  milestone update that milestone's doc instead of creating a new one.

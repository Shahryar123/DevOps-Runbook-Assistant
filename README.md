# Runbook Assistant

An AI chat assistant for DevOps troubleshooting. You ask a question like *"My IIS app pool keeps stopping, what do I check?"* and it answers using our own runbooks (step-by-step troubleshooting guides) in `data/runbooks/`.

## How it works

1. **Runbooks**: markdown guides for common problems, stored in `data/runbooks/`.
2. **Ingest**: the runbooks are checked, split into small pieces and stored in a local search database (Chroma). Only new or changed runbooks are processed again.
3. **Route**: your question is sorted into a topic (deployment, networking, security, cicd) so the search looks in the right runbooks first.
4. **Search and check**: the most relevant pieces are found, and the model checks whether each one actually helps.
5. **Retry or answer**: if nothing useful is found, the question is reworded with more technical terms and searched again across all runbooks. If there is still nothing, the assistant says "I don't have a runbook for this" instead of guessing. Otherwise an OpenAI model writes the answer from the useful pieces.

6. **Guardrails**: passwords and keys pasted into a question are hidden before anything is sent, harmful or manipulative questions are refused, risky commands (like `iisreset`) come with a warning, and the number of questions is limited.

See the [flow diagram](docs/graph.md).

## Getting started

```powershell
cp .env.example .env                    # add your OPENAI_API_KEY
uv sync                                 # install dependencies
uv run python scripts/check_setup.py    # check that your keys work
```

## Folders

| Folder | What's inside |
|---|---|
| `src/runbook_assistant/` | The application code |
| `data/runbooks/` | The troubleshooting guides the assistant learns from |
| `scripts/` | Helper scripts (e.g. key check) |
| `tests/` | Automated tests |
| `evals/` | Test questions and scoring used to measure answer quality in LangSmith |
| `docs/milestones/` | One document per build step, explaining what changed and why |

## Progress

- [x] 1. [Project skeleton](docs/milestones/01-project-skeleton.md)
- [x] 2. [Basic RAG](docs/milestones/02-basic-rag.md)
- [x] 3. [LangGraph with relevance grading](docs/milestones/03-langgraph-grading.md)
- [x] 4. [Topic router and query-rewrite loop](docs/milestones/04-router-rewrite.md)
- [x] 5. [Evaluation with LangSmith](docs/milestones/05-evaluation.md)
- [x] 6. [Streamlit chat UI](docs/milestones/06-streamlit-ui.md)
- [x] 7. [Guardrails](docs/milestones/07-guardrails.md)
- [x] 8. [Ingest data pipeline](docs/milestones/08-data-pipeline.md)

## Try it

```powershell
uv run python -m runbook_assistant.ingest                 # load new/changed runbooks into the search database
uv run python -m runbook_assistant.ingest --dry-run       # preview what would change (no cost)
uv run python scripts/ask.py "IIS app pool keeps stopping" # ask a question
uv run streamlit run src/runbook_assistant/app.py          # chat in the browser
```

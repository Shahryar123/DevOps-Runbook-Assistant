# Runbook Assistant

An AI chat assistant for DevOps troubleshooting. You ask a question like *"My IIS app pool keeps stopping, what do I check?"* and it answers using our own runbooks (step-by-step troubleshooting guides) in `data/runbooks/`.

## How it works

1. **Runbooks**: markdown guides for common problems, stored in `data/runbooks/`.
2. **Ingest**: the runbooks are split into small pieces and stored in a local search database (Chroma).
3. **Ask**: when you ask a question, the most relevant pieces are found and given to an OpenAI model, which writes the answer.
4. **Check**: before answering, the model checks whether each piece is actually relevant. If none are, the assistant says "I don't have a runbook for this" instead of guessing. See the [flow diagram](docs/graph.md).

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
| `docs/milestones/` | One document per build step, explaining what changed and why |

## Progress

- [x] 1. [Project skeleton](docs/milestones/01-project-skeleton.md)
- [x] 2. [Basic RAG](docs/milestones/02-basic-rag.md)
- [x] 3. [LangGraph with relevance grading](docs/milestones/03-langgraph-grading.md)
- [ ] 4. Query rewrite and topic router

## Try it

```powershell
uv run python -m runbook_assistant.ingest                 # load runbooks into the search database
uv run python scripts/ask.py "IIS app pool keeps stopping" # ask a question
```

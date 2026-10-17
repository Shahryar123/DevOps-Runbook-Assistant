# Milestone 1 — Project Skeleton

**Status:** Done
**Goal:** Set up the folder structure, typed configuration and a script that checks the API keys, and add three sample runbooks. This milestone has no RAG and no graph.

---

## What changed

| File | Purpose |
|---|---|
| `src/runbook_assistant/config.py` | All settings (keys, model names, paths) loaded from `.env` |
| `scripts/check_setup.py` | Makes one chat call and one embedding call to prove the keys work |
| `src/runbook_assistant/ingest.py` | Placeholder (milestone 2) |
| `src/runbook_assistant/graph/__init__.py` | Placeholder package for LangGraph code |
| `evals/.gitkeep` | Placeholder for LangSmith datasets/evaluators |
| `tests/test_config.py` | Checks that settings load, without using the real `.env` |
| `data/runbooks/*.md` | 3 sample runbooks (IIS, AWS EC2, Azure DevOps) |

Resulting layout:

```
runbook-assistant/
├── src/runbook_assistant/
│   ├── __init__.py
│   ├── config.py
│   ├── ingest.py          (stub)
│   └── graph/__init__.py  (stub)
├── scripts/check_setup.py
├── data/runbooks/
│   ├── iis-app-pool-crashes.md
│   ├── aws-ec2-unreachable.md
│   └── azure-devops-pipeline-failures.md
├── evals/
├── tests/test_config.py
└── docs/milestones/
```

---

## How it works

### `config.py`

```python
class Settings(BaseSettings):
    openai_api_key: SecretStr
    langsmith_tracing: bool = False
    langsmith_api_key: SecretStr | None = None
    langsmith_project: str = "runbook-assistant"
    answer_model: str = "gpt-5.4-mini"
    router_model: str = "gpt-5.4-mini"
    embedding_model: str = "text-embedding-3-small"
    runbooks_dir: Path = PROJECT_ROOT / "data" / "runbooks"
    chroma_dir: Path = PROJECT_ROOT / "chroma_db"
```

- Each field is filled from an environment variable with the same name in any case, so `openai_api_key` comes from `OPENAI_API_KEY`. If the environment variable isn't set, the value comes from `.env`. Fields with defaults can be overridden the same way, for example `ANSWER_MODEL=...`.
- `PROJECT_ROOT` is worked out from the location of `config.py`, so the paths are correct whatever directory you run from.
- `get_settings()` uses `@lru_cache`, so the whole app shares one `Settings` object.
- `configure_tracing(settings)` copies the LangSmith values into `os.environ` (see the concepts below for why).

The rest of the code should always get config with `get_settings()`. Don't hardcode keys, model names or paths.

### `scripts/check_setup.py`

1. Loads the settings and turns on tracing.
2. `check_chat`: `ChatOpenAI(model=answer_model).invoke("Reply with exactly: OK")`. This uses `gpt-5.4-mini`, the model that will write the answers.
3. `check_embeddings`: `OpenAIEmbeddings(model=embedding_model).embed_query("ping")`
4. Prints PASS or FAIL for each check and exits with code 1 if either one fails. It never prints the key.
### Sample runbooks

All three use the same section headings:

`# Title` → Symptoms → Likely Causes → Diagnosis Steps → Resolution → Escalation

Using the same headings is deliberate. In milestone 2, the ingest step can split each file into chunks by heading, so each chunk is a meaningful section such as "Diagnosis Steps for EC2".

---

## New concepts

- **pydantic-settings / `BaseSettings`.** A Pydantic model that fills its fields from environment variables and `.env`, and checks their types. If a required value such as `OPENAI_API_KEY` is missing, it raises a clear error at startup instead of failing somewhere later.
- **`SecretStr`.** Shows `**********` when printed or logged. You only get the real value by calling `.get_secret_value()`. LangChain accepts `SecretStr` directly as `api_key`.
- **Why `configure_tracing()` exists.** pydantic-settings reads `.env` into the `Settings` object but does **not** set environment variables. LangSmith tracing only looks at `os.environ["LANGSMITH_*"]`, so we copy the values there once at startup.
- **`ChatOpenAI`.** LangChain's wrapper around OpenAI chat models. You pass it text or a list of messages and get back an `AIMessage`, with the text in `.content`.
- **Runnable / `.invoke()`.** Every LangChain component (models, prompts, retrievers, chains, LangGraph graphs) shares the same interface: `.invoke()`, `.batch()` and `.stream()`. Because they share an interface, they can be connected together.
- **`OpenAIEmbeddings`.** Turns text into a vector (1536 numbers for `text-embedding-3-small`). Texts that mean similar things get vectors that are close together, which is how RAG finds the relevant runbook sections.
- **LangSmith tracing.** With `LANGSMITH_TRACING=true`, every LangChain call is logged to LangSmith along with its inputs, outputs, latency and token usage. You don't need to change any code.

---

## How to run / verify

```powershell
uv run ruff check . --fix; uv run ruff format .
uv run pytest                          # 1 passed
uv run python scripts/check_setup.py   # expect two [PASS] lines
```

Result when this milestone was finished:

```
LangSmith tracing: on
[PASS] chat (gpt-5.4-mini): OK
[PASS] embeddings (text-embedding-3-small): dimension=1536
```

---

## Notes and decisions

- **Embedding model.** CLAUDE.md doesn't name an embedding model, so this milestone uses `text-embedding-3-small`. To change it, set `EMBEDDING_MODEL` in `.env`. If you change it after ingesting, re-run ingest, because vectors from different models can't be compared.
- **`# noqa: BLE001` in `check_setup.py`.** Ruff normally rejects catching every error with `except Exception`. Here it's on purpose: a diagnostic script should report any failure, not crash.
- **Python version.** CLAUDE.md says 3.12, but `.python-version` and `pyproject.toml` use 3.13. This hasn't been fixed yet; one of them should be updated so they agree.

## Next milestone

**Milestone 2 — Ingest:** load the runbooks, split them by heading, embed the chunks and store them in Chroma at `chroma_dir`.

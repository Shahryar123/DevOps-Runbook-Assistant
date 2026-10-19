# Milestone 9 — Containers and a switchable vector store (deployment Phase A)

**Status:** Done. 232 tests pass (Azure fully mocked). The Docker image builds, and the app and ingest run locally with Chroma via `docker compose`. Not yet tested against a real Azure AI Search service.
**Goal:** Package the app as one Docker image that runs both the web app and the ingest job, and make the vector store switchable (local Chroma or Azure AI Search), so Phase B can deploy to Azure Container Apps without changing code. No Azure resources exist yet.

---

## What changed

| File | Purpose |
|---|---|
| `Dockerfile` | Two-stage build (uv → slim runtime), non-root, health check; app by default, ingest by overriding the command |
| `.dockerignore` | Keeps secrets (`.env*`), local data, caches, tests, docs and git out of the build |
| `docker-compose.yml` | Local dev: `app` service, plus an `ingest` service under the `ingest` profile, sharing a named volume |
| `src/runbook_assistant/vectorstores/` | **New.** `VectorIndex` interface (`base.py`), `ChromaIndex`, `AzureSearchIndex`, `factory.py` |
| `retriever.py` | `get_retriever()` and `list_runbooks()` go through `get_vector_index()`; no Chroma code left |
| `pipeline/` | Writes through `VectorIndex`; pluggable `ManifestStore`; chunk metadata gains `index_fingerprint`; the lock file applies to Chroma only |
| `config.py` | `VECTOR_STORE`, `RETRIEVAL_MODE`, `AZURE_SEARCH_*`, `EMBEDDING_DIMENSIONS`; `load_settings()` raises one `ConfigError` listing every problem |
| `app.py`, `ingest.py` | Show the `ConfigError` message (app: on the page; ingest: on stderr, exit 1) |
| `service.py` | "Has ingest run?" check works for both stores |
| `evals/run_evals.py` | Experiment metadata includes `vector_store` and `retrieval_mode` |
| `pyproject.toml` / `uv.lock` | **Added:** `langchain-azure-ai`. **Removed:** `langchain-community` and `python-dotenv`, no longer imported (pydantic-settings still installs `python-dotenv` for `.env` files) |
| `tests/test_vectorstores.py`, `tests/test_config.py`, `tests/test_retriever.py` | Factory, Azure adapter (mocked), index-derived manifest, Azure-path pipeline run, 12-factor config |

### New settings
| Env var | Default | |
|---|---|---|
| `VECTOR_STORE` | `chroma` | `chroma` or `azure_search` |
| `RETRIEVAL_MODE` | `vector` | `hybrid` (keyword + vector) needs `azure_search`; otherwise a config error |
| `AZURE_SEARCH_ENDPOINT` | – | `https://<name>.search.windows.net`; required for `azure_search` |
| `AZURE_SEARCH_INDEX_NAME` | – | Required for `azure_search` (the index is created on the first ingest) |
| `AZURE_SEARCH_API_KEY` | – | Optional, for local testing. Without it: `DefaultAzureCredential` |
| `EMBEDDING_DIMENSIONS` | `1536` | Vector size of `text-embedding-3-small`; given up front so opening the index needs no embedding call |

---

## How it works

### One image, two jobs
```
                      runbook-assistant image
          ┌──────────────────────┴──────────────────────┐
  default command                                 command override
  streamlit run … (port 8501)                     python -m runbook_assistant.ingest
  = Container App (Phase B)                       = Container Apps Job (Phase B)
          │                                               │
          └──── same VECTOR_STORE → Chroma volume (local) or Azure AI Search ────┘
```

### The vector store switch
```
retriever / graph / UI / pipeline ──► VectorIndex interface
        as_retriever(k, filter) · add_documents · delete · count · list_metadata · reset
                       │
       factory: VECTOR_STORE ─┬─ chroma        → ChromaIndex       (folder; JSON manifest; lock file)
                              └─ azure_search  → AzureSearchIndex  (service; manifest from the index)
```

**Azure AI Search adapter.** It uses `AzureSearch` from **`langchain-azure-ai`**, which LangChain and Microsoft maintain in the `langchain-azure` repo. The older `langchain_community` version lives in a package that's being sunset.
- **Auth.**
  - With `AZURE_SEARCH_API_KEY` set, an API key is used.
  - Otherwise `DefaultAzureCredential` tries, in order: Managed Identity (in Azure), environment credentials, then `az login` (locally).
  - No key appears in code or the image.
- **Index fields.**
  - The library's required fields: `id` (key), `content`, `content_vector`, and `metadata` as JSON.
  - Our metadata as **filterable** fields: `source`, `title`, `topic`, `section`, `content_hash`, `ingested_at` and `chunk_id`.
  - The router's topic filter becomes the OData filter `topic eq 'networking'`. Values are quote-escaped and field names validated.
- **Keys.** Azure document keys allow only letters, digits, `-`, `_` and `=`, so `iis-app-pool-crashes.md::0` is stored URL-safe base64-encoded. The original ID stays in `chunk_id`, and the pipeline keeps using `file::N`.
- **`RETRIEVAL_MODE`.** `vector` uses the library's `similarity` search; `hybrid` uses keyword (BM25) + vector, merged by Azure. Chroma only supports vector.
- **Reset** (full rebuild) deletes and recreates the index, so a schema change such as a new embedding size is safe.

**Manifest per store.**
- **Chroma:** `chroma_db/manifest.json`, unchanged.
- **Azure (option B, approved):** derived from the index. Every chunk carries `source`, `content_hash`, `chunk_id`, `ingested_at` and `index_fingerprint` (the settings fingerprint as JSON), so one metadata query rebuilds the manifest at the start of each run, and `save()` is a no-op.
  - The index is the single source of truth: no file can drift from it, and the job's temporary disk doesn't matter.
  - If chunks disagree, that's handled safely: a file with mixed hashes is treated as changed, and mixed fingerprints trigger a rebuild.

**Only one writer.** For Chroma, the lock file (`chroma_db/.ingest.lock`) still applies. For Azure, there's no shared disk to lock, so **Phase B must configure the Container Apps Job with `parallelism: 1` and `replicaCompletionCount: 1`** and no overlapping schedules.

**Config (12-factor).** Every setting is an environment variable; lists and dicts are written as JSON, e.g. `RUNBOOK_EXPECTED_SECTIONS='["Symptoms"]'`. With no `.env`, the app starts from the environment alone, as it will in Azure. Missing settings produce one message:
```
Invalid configuration:
  - missing required settings: OPENAI_API_KEY, AZURE_SEARCH_ENDPOINT (needed when VECTOR_STORE=azure_search), …
Set these as environment variables (or in .env for local development).
```

---

## New concepts

- **Image vs container.** An *image* is a read-only package: the OS files, Python, the dependencies and our code. A *container* is a running instance of an image, with its own process, network and writable scratch layer. You build the image once and start as many containers from it as needed. When a container is removed, its scratch layer goes with it.
- **Layers and caching.** Each Dockerfile instruction creates a layer. When rebuilding, Docker reuses every layer up to the first one whose inputs changed. That's why `pyproject.toml` and `uv.lock` are copied and installed **before** the source: editing code only rebuilds the last few layers (seconds), while changing dependencies rebuilds the slow install layer.
- **Multi-stage build.** The `builder` stage has uv and the build caches. The final stage copies only `/app` (the venv and our code), so the build tools never ship in the image.
- **Volumes.** Storage that lives outside the container and survives restarts and rebuilds. The `chroma-data` named volume holds the index: `docker compose down` keeps it, `docker compose down -v` deletes it. In Azure the index lives in Azure AI Search instead, so no volume is needed there.
- **Health checks.** A command Docker runs periodically (here, a request to Streamlit's `/_stcore/health`). After repeated failures the container is marked *unhealthy*, so orchestrators can restart it or stop sending traffic. Azure Container Apps uses its own liveness and readiness probes, configured in Phase B.
- **Why non-root.** Containers share the host's kernel. Running as an unprivileged user (`app`, uid 10001) limits what an attacker could do through a bug in the app or a dependency. The code is also owned by root, so the app can't modify itself; only `/app/chroma_db` is writable.
- **Why the same image for app and job.** The code, dependencies and config handling are identical for both, so the ingest job can never be built from a different version than the app it feeds. One build, one vulnerability scan, one tag to deploy; only the command differs.
- **Compose profiles.** Services with a `profiles:` list don't start with a plain `docker compose up`. They run only when named (`docker compose run --rm ingest`) or when the profile is enabled. That suits a one-off job next to a long-running app.
- **No secrets in the image.** Anyone who can pull an image can read every file in it. So `.env` is excluded by `.dockerignore`, and secrets are passed at run time (`env_file` locally, Key Vault and Managed Identity in Phase B).

---

## How to run

```powershell
docker compose build
docker compose run --rm ingest          # first run: full rebuild into the chroma-data volume
docker compose up app                   # http://localhost:8501
docker compose down                     # stop; add -v to also delete the index volume
```

Without compose:
```powershell
docker build -t runbook-assistant .
docker run --rm --env-file .env -v chroma-data:/app/chroma_db runbook-assistant python -m runbook_assistant.ingest
docker run --rm -p 8501:8501 --env-file .env -v chroma-data:/app/chroma_db runbook-assistant
```

Trying Azure AI Search locally, once you have a Search service: add `VECTOR_STORE=azure_search`, `AZURE_SEARCH_ENDPOINT`, `AZURE_SEARCH_INDEX_NAME` and `AZURE_SEARCH_API_KEY` to `.env`, then run ingest and the app as above. Optionally add `RETRIEVAL_MODE=hybrid`.

### Checks
```powershell
uv run ruff check . --fix; uv run ruff format .
uv run pytest            # 232 passed (no OpenAI or Azure calls)
```

---

## Troubleshooting

- **`failed to solve: changes out of order: "data/runbooks" "README.md"`** (Windows, `docker compose build`). This is a Docker Desktop bug while sending the project folder to the builder, not a problem with the Dockerfile. Update Docker Desktop and retry. If it persists, build with `docker build -t runbook-assistant:local .`; compose then reuses that image.

## Notes and decisions

- **Python 3.13, not 3.12.** `pyproject.toml` requires ≥3.13 and `uv.lock` is resolved for it. The base is `python:3.13.14-slim-trixie`. Check that this exact tag exists on Docker Hub when you first build; for fully reproducible builds, pin it by digest as well (`python:3.13.14-slim-trixie@sha256:…`).
- **uv 0.13.1** is the version that wrote `uv.lock`, so `--frozen` reads it exactly.
- **Editable install.** `uv sync` installs the project in editable mode pointing at `/app/src`, so `config.PROJECT_ROOT` is `/app`. `RUNBOOKS_DIR` and `CHROMA_DIR` are set explicitly in the image anyway.
- **Runbooks in the image.** The image contains `data/runbooks`, which the Azure job will use. Compose mounts your working copy over it, so local edits don't need a rebuild.
- **Image size.** `langchain-azure-ai` adds about 30–60 MB of Azure SDK packages. Chroma users carry them too, but they're only *imported* when `VECTOR_STORE=azure_search`.
- **Chroma + running app.** It's still single-process: stop the app, ingest, then restart (see Milestone 8). Azure AI Search doesn't have this limitation.
- **Fingerprint gained `vector_store`.** The first ingest after this milestone does a full rebuild ("settings changed: vector_store: (none) -> chroma"), and so does switching stores.
- **Not tested against real Azure.** The adapter is covered by mocks of the SDK. The first real check is a local run with an API key against a Search service in Phase B.

## Next: Phase B

Create the Azure resources: Container Registry, Container Apps environment, the app, the ingest **Job** (`parallelism: 1`), Azure AI Search, Key Vault, a managed identity with *Search Index Data Contributor* and *Search Service Contributor*, and Log Analytics. Then push the image and set the environment variables and secrets, with probes on `/_stcore/health`.

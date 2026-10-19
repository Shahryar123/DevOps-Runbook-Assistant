# Milestone 8 — Ingest data pipeline

**Status:** Built and tested offline (208 tests; the pipeline tests use a real temporary Chroma and fake embeddings). Not yet run on the real index.
**Goal:** Turn "delete everything and re-embed" into a proper data pipeline. It validates runbooks before touching anything, only re-embeds what changed, keeps a manifest of what's in the index, and reports what it did and what it cost. **Retrieval doesn't change:** same chunks, same chunk IDs, same embedding model.

---

## What changed

| File | Purpose |
|---|---|
| `src/runbook_assistant/pipeline/sources.py` | `DocumentSource` interface (`list_ids`, `read`) and `LocalFolderSource` |
| `pipeline/chunking.py` | Frontmatter parsing and splitting, **moved unchanged** from `ingest.py` |
| `pipeline/validation.py` | All checks; errors stop the run, warnings are reported |
| `pipeline/manifest.py` | Manifest model, settings fingerprint, SHA-256 hashing, atomic writes |
| `pipeline/sync.py` | `plan_sync` (what to do), `CachingEmbeddings` (embed first), `apply_plan` (Chroma writes) |
| `pipeline/report.py` | Report model, console output, token counting and cost |
| `pipeline/lock.py` | Lock file so only one ingest writes at a time |
| `pipeline/run.py` | Wires the steps together; dependencies are passed in so tests can fake them |
| `pipeline/__init__.py` | `PIPELINE_VERSION` |
| `src/runbook_assistant/ingest.py` | Now just the CLI: flags, real dependencies, exit code |
| `config.py` | `runbook_max_bytes`, `runbook_expected_sections`, `embedding_batch_size`, `embedding_price_per_1m_tokens`; `manifest_path`, `ingest_report_path`, `ingest_lock_path` (all under `chroma_dir`) |
| `tests/test_pipeline.py` | 32 tests |

---

## How it works

```
python -m runbook_assistant.ingest [--dry-run | --validate-only] [--full]

 source ─► VALIDATE ── any error? ──► list every problem, exit 1, nothing touched
              │                          (--validate-only stops here: exit 0/1)
              ▼
           PLAN: hash each file, compare with manifest.json
              new / changed / unchanged / deleted   (or full rebuild + reason)
              │──── --dry-run stops here: prints the plan and the token/cost estimate
              ▼
           LOCK chroma_db/.ingest.lock (exit 3 if another ingest holds it)
              ▼
           EMBED new + changed chunks in batches (the only network calls)
              │──── failure: nothing written, index + manifest unchanged, exit 2
              ▼
           APPLY to Chroma: upsert new/changed, delete stale and deleted chunk IDs
              ▼
           MANIFEST saved atomically ─► REPORT (console + chroma_db/ingest_report.json)
```

### Validation
| Check | Result |
|---|---|
| Empty file, file over `runbook_max_bytes` | error |
| Missing or invalid frontmatter, no `title`, `topic` not in `runbook_topics`, no content after the frontmatter | error |
| Duplicate titles across runbooks (case-insensitive) | error |
| Injection patterns (`guardrails.injection`) | error |
| Missing expected section (Symptoms, Likely Causes, Diagnosis, Resolution; matched by heading prefix) | warning |
| Risky commands (`guardrails.commands`) | warning |

Every issue is listed as `file: issue` before the run stops, so one run shows all the problems, not just the first.

### Manifest (`chroma_db/manifest.json`)
```json
{
  "format": 1,
  "fingerprint": {"embedding_model": "text-embedding-3-small", "chunk_size": 800,
                  "chunk_overlap": 100, "header_levels": "#,##,###",
                  "splitter": "markdown-headers+recursive-character",
                  "collection": "runbooks", "pipeline_version": 1},
  "files": {
    "iis-app-pool-crashes.md": {"content_hash": "3f2a…", "chunk_ids": ["iis-app-pool-crashes.md::0", "…"],
                                "chunk_count": 16, "ingested_at": "2026-10-19T14:02:11+00:00"}
  },
  "updated_at": "…"
}
```
Every chunk in Chroma also gets `content_hash` and `ingested_at` in its metadata. The retriever only filters on `topic`, so these extra fields don't change retrieval.

### When it rebuilds everything
- `--full` was passed
- there's no readable manifest (first run, or it was deleted or corrupted)
- the fingerprint changed (the report names the setting, e.g. `settings changed: chunk_size: 800 -> 600`)
- the index chunk count doesn't match the manifest (someone changed the index by hand)

### Flags and exit codes
| | Embeds | Writes the index | Writes manifest/report |
|---|---|---|---|
| *(none)* | new + changed only | yes | yes |
| `--full` | everything | yes (reset first) | yes |
| `--dry-run` (can be combined with `--full`) | no | no | no |
| `--validate-only` | no | no | no |

Exit codes: **0** OK, **1** validation failed, **2** embedding or index write failed, **3** another ingest is running.

### Running ingest while the app is open
The local Chroma store (`PersistentClient`) is built for **one process**. The Streamlit app keeps its own copy open and won't reliably see ingest's changes; after a full rebuild its handle can even point to a deleted collection. So:
1. Stop the app.
2. Run ingest.
3. Start the app again.

The report ends with a reminder whenever chunks were added or removed. The lock file stops two ingests from writing at once. The proper fix, a Chroma **server** that the app and ingest both talk to, is planned for a later milestone.

---

## New concepts

- **Content hashing.** SHA-256 turns a file's bytes into a 64-character fingerprint. Same bytes give the same hash, and any change gives a completely different one. Comparing hashes tells you exactly which files changed, without reading the index or calling OpenAI.
- **Manifest.** A small record of what's in the index and how it was built. The vector store holds vectors, not the reasons it holds them. The manifest answers "which file produced which chunk IDs, from which content, with which settings", which is what's needed to delete a removed file's chunks or skip an unchanged one.
- **Incremental sync.** Only do work for what changed: new files are embedded and added, changed files re-embedded and upserted (stale IDs deleted), deleted files' chunks removed, unchanged files skipped. Cost and time grow with the size of the *change*, not the size of the corpus.
- **Why settings changes force a rebuild.** Chunks made with `chunk_size=800` and chunks made with `600` cover different text, and vectors from two embedding models live in different "spaces" where distances mean nothing to each other. Mixing them would silently give bad search results. The fingerprint stores the settings, and any difference means everything is re-chunked and re-embedded.
- **Atomic writes.** The manifest is written to a temp file in the same folder, then renamed over the old one (`os.replace`). A rename either happens completely or not at all, so a crash leaves the old manifest or the new one, never half a file. It's saved only after the Chroma writes, and records only what was actually applied.
- **Embed first, then write.** Every embedding call happens before the first Chroma write (`CachingEmbeddings` pre-computes vectors in batches, then serves them to Chroma from memory). An API outage therefore can't leave the index half-updated, and the manifest can't claim something that isn't there. Identical chunk texts are embedded only once.
- **Idempotency.** Running it again with nothing changed does nothing: 0 embedding calls, and the manifest isn't rewritten. That makes it safe to run on a schedule or in CI.
- **Dry runs.** Do all the reading and planning, stop before the first side effect, and print what *would* happen with the token and cost estimate. Use it to check a big change before paying for it.
- **Source abstraction.** The pipeline only uses `list_ids()` and `read()`. An `AzureBlobSource` or `GitRepoSource` could be added later without changing validation, sync or reporting.

---

## How to run

```powershell
uv run python -m runbook_assistant.ingest --validate-only   # check the runbooks (exit 0/1)
uv run python -m runbook_assistant.ingest --dry-run         # what would change, what it would cost
uv run python -m runbook_assistant.ingest                   # sync
uv run python -m runbook_assistant.ingest --full            # rebuild everything
```

**First run after this milestone:** there's no manifest yet, so it does one full rebuild (reason: "no readable manifest") and writes `chroma_db/manifest.json`. After that, runs are incremental.

Example output (illustrative numbers):
```
=== Ingest report (sync) ===
Validation: 0 error(s), 2 warning(s)
  warning iis-app-pool-crashes.md: risky command: iisreset (medium)
  ...
Files: 0 new, 1 changed, 5 unchanged, 0 deleted
  changed iis-app-pool-crashes.md
Chunks: 16 added, 0 removed, 77 total
Embedding: 2,431 tokens, est. $0.000049, 1 API call(s)
Duration: 1.84 s

If the Streamlit app is running, restart it to load the changes.
```

Token counts come from `tiktoken` (the tokenizer OpenAI uses). If it can't load its data offline, the report shows `~` and estimates about 4 characters per token.

### Checks
```powershell
uv run ruff check . --fix; uv run ruff format .
uv run pytest            # 208 passed (no API calls)
```

---

## Notes and decisions

- **All-or-nothing embedding.** If any batch fails, nothing is written, not even files whose batches succeeded. That's simplest and always consistent, and re-embedding a few runbooks is cheap. For a large corpus, this could be changed to commit file by file.
- **Chunk IDs stay `file::N`.** A changed file's new chunks overwrite the same IDs (upsert), and leftover IDs from a longer old version are deleted. Renaming a file counts as delete + new.
- **`PIPELINE_VERSION` is a code constant**, not a setting. Bump it when chunking or metadata code changes the stored chunks, and the next run rebuilds.
- **Report on failure.** A sync run writes `ingest_report.json` even when validation fails or embedding errors, so CI can keep it as an artifact. Dry runs and validate-only runs write nothing.
- **Stale lock.** If an ingest crashes hard, `chroma_db/.ingest.lock` stays behind. The error message says when it was taken. Delete it once you're sure no ingest is running.
- **Tests use a real Chroma.** A temporary folder and `DeterministicFakeEmbedding` exercise the real upsert, delete and reset paths, not mocks of them.

## Next milestone

Run `--dry-run`, then the first sync, and re-run the evals to confirm retrieval didn't change. Possible next steps: `--validate-only` in GitHub Actions, the Chroma server (safe ingest while the app runs), or another document source.

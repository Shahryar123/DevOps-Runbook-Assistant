# Milestone 5 — Evaluation with LangSmith

**Status:** Built and tested offline. The dataset hasn't been uploaded and no experiments have been run yet.
**Goal:** Compare versions of the assistant with real numbers instead of impressions. The same 25 questions go through each setup, are scored by the same 4 evaluators, and the results are compared side by side in LangSmith.

---

## What changed

| File | Purpose |
|---|---|
| `src/runbook_assistant/config.py` | Flags `routing_enabled` and `grading_enabled` (alongside `max_retries`); `judge_model`; eval settings (`eval_dataset_name`, `eval_dataset_path`, `eval_max_concurrency`, `eval_categories`) |
| `src/runbook_assistant/graph/build.py` | `GraphOptions` (the 3 flags). `build_graph(options)` and `ask_graph(question, options)` |
| `src/runbook_assistant/graph/nodes.py` | `route_question` and `grade_documents` do nothing (no LLM call) when their flag is off |
| `evals/dataset.jsonl` | 25 test cases, committed to git |
| `evals/upload_dataset.py` | Creates or syncs the LangSmith dataset. Safe to run repeatedly |
| `evals/evaluators.py` | `retrieval_hit`, `correct_refusal`, `correctness`, `groundedness` |
| `evals/run_evals.py` | `--setup chain\|graph\|graph_routing` → one LangSmith experiment, plus a printed summary |
| `tests/test_evaluators.py` | 18 tests: code evaluators, the shortcuts that skip the judge, dataset validity |
| `tests/test_graph.py` | 2 new tests: routing off and grading off make no LLM calls |

### The flags

| Flag (env var) | Default | When off |
|---|---|---|
| `ROUTING_ENABLED` | `true` | No router call. Topic = `general`, so the search covers every topic |
| `GRADING_ENABLED` | `true` | No grader calls. Every chunk is kept, so a rewrite or refusal never happens |
| `MAX_RETRIES` | `1` | `0` turns off the rewrite loop |

`ask.py` respects these too. For example, `$env:GRADING_ENABLED="false"; uv run python scripts/ask.py "..."`.

### Dataset mix

| Category | Count | What it tests |
|---|---|---|
| `clear` | 12 | Precise technical questions, 2 per runbook |
| `vague` | 5 | Casual wording with no technical terms. Tests the router and the rewrite |
| `out_of_scope` | 5 | Kubernetes, Docker Swarm, Oracle, RDS, Jenkins. The assistant must refuse |
| `multi_runbook` | 3 | Needs facts from two runbooks |

Each line looks like this:

```json
{"id": "clear-01", "category": "clear", "question": "...", "reference_answer": "- key point\n- key point", "expected_sources": ["iis-app-pool-crashes.md"]}
```

`expected_sources: []` marks an out-of-scope question. `id` is a stable name: re-uploading updates the example with that `id` rather than adding a new one.

---

## How it works

```
dataset.jsonl --upload_dataset--> LangSmith dataset "runbook-assistant-eval" (25 examples)
                                          |
run_evals --setup X:  for each example:   v
   target(inputs) -> {answer, sources, contexts}      (chain or graph)
   4 evaluators(inputs, outputs, reference_outputs) -> scores
                                          |
                                          v
                     LangSmith experiment "graph-routing-xxxx" + metadata
                     + printed summary table (overall and per category)
```

### The three setups

| `--setup` | System | Experiment prefix |
|---|---|---|
| `chain` | `rag.py` baseline | `chain-baseline` |
| `graph` | graph with routing **off** | `graph-no-routing` |
| `graph_routing` | graph with routing **on** | `graph-routing` |

`grading_enabled` and `max_retries` come from config. If they aren't the defaults, the name gets a suffix, for example `graph-routing-nograde-r0`.

**Metadata on each experiment:** setup, all 6 models, `retriever_k`, `chunk_size`, `chunk_overlap`, the 3 flags and the dataset name.

### The evaluators

| Key | Type | Score | Not applicable (`None`) when |
|---|---|---|---|
| `retrieval_hit` | code | Fraction of `expected_sources` among the chunks the model saw | out of scope |
| `correct_refusal` | code | 1 if (refused ⇔ out of scope) | never |
| `correctness` | LLM judge | Fraction of the reference key points the answer covers. An in-scope question that was refused scores **0** without calling the judge | out of scope |
| `groundedness` | LLM judge | Fraction of the answer's claims that the chunks support | the answer is a refusal |

"The chunks the model saw" means every retrieved chunk for the chain, and only the **kept** chunks for the graph. Both setups are judged on the same basis: the text that was actually in the prompt.

---

## New concepts

- **Dataset.** A named, versioned collection of test cases stored in LangSmith. We keep the source in git (`dataset.jsonl`), so changes are reviewed like code, and sync it to LangSmith with `upload_dataset.py`.
- **Example.** One test case in a dataset, with three parts:
  - `inputs`: what the system receives (`{question}`)
  - `outputs`: the **reference** the evaluators compare against (`{reference_answer, expected_sources}`)
  - `metadata`: tags for grouping (`{category, case_id}`)
- **Experiment.** One run of one system version over the whole dataset. Each example produces a traced run plus its scores. The experiment metadata records exactly which configuration produced the scores, so you can reproduce them later.
- **Evaluator.** A function that scores a single example. It receives `inputs`, `outputs` (what the system returned) and `reference_outputs`, and returns `{key, score, comment}`.
  - **Code evaluators** (`retrieval_hit`, `correct_refusal`) are exact, free, fast and always give the same result. Use them whenever a rule can decide.
  - **LLM-as-judge evaluators** (`correctness`, `groundedness`) use a model to grade things code can't, such as "does this answer cover these points?". Structured output (`JudgeScore{reason, score}`) means each verdict is a number with a reason, not free text.
- **Limits of LLM-as-judge:**
  - **Noise.** The same answer can score 0.8 one time and 0.9 the next, so ignore small differences (about 0.05) between experiments.
  - **Bias.** Judges tend to favour longer, confident answers, and a judge from the same model family as the system may favour its own style. Here both are `gpt-5.4-mini`. Switch `JUDGE_MODEL` to a stronger or different model if scores look suspiciously high.
  - **The rubric matters.** The judge is only as good as the reference key points. A vague reference produces vague scores.
  - **Cost.** Two judge calls per in-scope example.
  - **Check it yourself.** Read the judge's `reason` on a few examples, especially low and high scores, before trusting the averages.
- **`evaluate()`.** `langsmith.evaluate(target, data=dataset, evaluators=[...], experiment_prefix=..., metadata=...)` runs the target on every example (in parallel, up to `eval_max_concurrency`), runs every evaluator, and uploads it all as one experiment.

### Comparing experiments in the LangSmith UI

1. Open **Datasets & Experiments** → `runbook-assistant-eval`. The **Experiments** tab lists every run, with the average of each evaluator as a column.
2. Tick two or more experiments (for example `chain-baseline-…` and `graph-routing-…`) and click **Compare**.
3. The comparison view shows one row per question and one column per experiment. Cells that are better or worse than the baseline are highlighted.
4. Click a cell to open the full trace. You can see what was retrieved, what the grader kept and what the judge's reason was.
5. Use **Filter** to focus on a group, such as `metadata.category = out_of_scope`, or `correct_refusal < 1` to find failures.
6. The metadata column shows the flags and models behind each experiment, so a better score can always be traced back to a specific configuration.

---

## How to run

```powershell
uv run ruff check . --fix; uv run ruff format .
uv run pytest                                   # 45 passed (no API calls)

uv run python -m evals.upload_dataset           # creates/syncs the dataset (25 examples)
uv run python -m evals.run_evals --setup chain
uv run python -m evals.run_evals --setup graph
uv run python -m evals.run_evals --setup graph_routing

# Optional ablations (switching features off one at a time):
$env:GRADING_ENABLED="false"; uv run python -m evals.run_evals --setup graph_routing; Remove-Item Env:GRADING_ENABLED
$env:MAX_RETRIES="0";         uv run python -m evals.run_evals --setup graph_routing; Remove-Item Env:MAX_RETRIES
```

Run these from inside `runbook-assistant\`, using `-m`, so Python can find the `evals` package. Re-run ingest first if the runbooks have changed.

### What to look for
- **`correct_refusal` on `out_of_scope`.** This is where grading should beat the chain.
- **`retrieval_hit` on `vague`.** This is where the router and the rewrite should help.
- **`groundedness`.** If it's high everywhere, the "answer only from context" rule is working.
- **`retrieval_hit` on `multi_runbook`.** Routing searches one topic first, so a question spanning two topics may only reach both runbooks after a retry.

---

## Notes and decisions

- **Numbering.** This is Milestone 5, because Milestone 4 was the router and rewrite loop.
- **`expected_sources` is a list.** multi_runbook cases need 2 files, and out-of-scope cases use `[]`.
- **Reference answers** contain only facts from the runbooks. `test_dataset_is_valid_and_points_at_real_runbooks` checks that every `expected_sources` entry names a real file, so renaming a runbook breaks the test rather than silently breaking evals.
- **Refusal detection** ignores case, punctuation and curly apostrophes, but the answer must **start with** the refusal text. An answer like "I don't have a runbook for this, but…" still counts as a refusal.
- **Same node set, no-op when off.** Disabling a feature doesn't remove its node, so the diagram, traces and code paths stay the same across setups, and only the behaviour differs.
- **Running as `-m evals.run_evals`.** `evals/` is a package (`__init__.py`) so tests can import `evals.evaluators`. This only works when run with `-m` from the project root.
- **`evals/.gitkeep`** has been removed because the folder now has real files.
- **Fixed: crashes from parallel runs.** `evaluate()` runs several questions at once in separate threads. On the first run, those threads created the local Chroma client at the same moment. chromadb can't handle that (`KeyError` / `Could not connect to tenant default_tenant`), so some questions crashed. `get_vector_store()` now creates the store **once, under a lock**, and reuses it. `tests/test_retriever.py` checks this with 8 threads.
- **Fixed: crashed runs were being scored.** LangSmith still calls the evaluators when a target crashes, just with no answer. That counted as "answered", so `correct_refusal` gave 1.0 and the others gave 0. Every evaluator now returns `None` (skipped) when there's no answer, and `run_evals` prints a **WARNING** listing the crashed cases. An experiment with that warning isn't safe to compare.
- **`TracerException('No indexed run ID …')` messages.** With `--setup graph`, the grader runs 4 calls at once (`.batch()`), and those calls happen inside `evaluate()`'s own parallel threads. LangChain's tracer sometimes can't link a call's end back to its start, and logs a warning. This only affects how those grader calls appear in the LangSmith trace tree. Answers, outputs and scores are unaffected. It couldn't be reproduced offline, so the code wasn't changed based on a guess. Instead, `run_evals` hides exactly this message and prints one line at the end saying how many were hidden. Any other warning or error still prints normally.

## Next milestone

Run the experiments and look at the numbers, then decide what to work on: the router prompt, grader leniency, hybrid search or `k`. Or build the Streamlit UI (`app.py`).

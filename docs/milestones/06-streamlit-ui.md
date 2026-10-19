# Milestone 6 — Streamlit chat UI

**Status:** Built and tested offline (AppTest). Not yet run against the real models.
**Goal:** A browser chat for the assistant that shows not just the answer, but where it came from (sources), how it was produced (topic, queries, retries, grading, latency, trace link), and lets users rate it in LangSmith.

---

## What changed

| File | Purpose |
|---|---|
| `src/runbook_assistant/app.py` | **New.** The Streamlit page. UI only: no RAG or graph logic |
| `src/runbook_assistant/service.py` | **New.** Plain-Python logic the UI needs: `answer()`, `trace_url()`, `send_feedback()`, `runbook_index()`, `friendly_error()`, `SETUPS` |
| `src/runbook_assistant/graph/build.py` | `ask_graph(..., run_id=None, tags=None)`. `GraphResult` gains `run_id`, `latency_s` |
| `src/runbook_assistant/rag.py` | `ask_with_sources(..., run_id=None, tags=None)`. `RagResult` gains `run_id`, `latency_s`. The chain run is named `runbook-chain`. `is_refusal` moved here from `evals/` |
| `src/runbook_assistant/retriever.py` | `list_runbooks()`: unique runbooks from Chroma metadata (no OpenAI call) |
| `src/runbook_assistant/config.py` | `langsmith_web_url`, `chain_run_name`, `ui_title`, `ui_default_setup`, `ui_tags`, `max_question_chars`, `feedback_key` |
| `evals/run_evals.py`, `evals/evaluators.py` | Reuse `service.SETUPS` / `options_for_setup` and `rag.is_refusal`. No behaviour change |
| `tests/test_service.py`, `tests/test_app.py` | Service logic, plus the whole page driven by Streamlit's AppTest (service faked) |

### New settings

| Setting (env var) | Default | Used for |
|---|---|---|
| `UI_DEFAULT_SETUP` | `graph_routing` | Setup selected when the page opens |
| `MAX_QUESTION_CHARS` | `500` | Longer questions get a friendly message instead of an answer |
| `UI_TAGS` | `["ui"]` | Added to every run started from the UI, so you can filter them in LangSmith |
| `FEEDBACK_KEY` | `user_rating` | Feedback key in LangSmith |
| `LANGSMITH_WEB_URL` | `https://smith.langchain.com` | Base of trace links (EU accounts: `https://eu.smith.langchain.com`) |
| `CHAIN_RUN_NAME` | `runbook-chain` | Trace name for the chain setup (the graph is `runbook-graph`) |
| `UI_TITLE` | `DevOps Runbook Assistant` | Page heading |

---

## How it works

```
browser ──► app.py (Streamlit: layout, session_state, widgets)
               │
               ├─ service.answer(question, setup) ──► ask_graph(...) or ask_with_sources(...)
               │       returns AnswerResult: answer, refusal?, topic, queries, retries,
               │       kept / filtered chunks, run_id, latency, trace_url
               ├─ service.send_feedback(run_id, 0|1, comment) ──► LangSmith create_feedback
               └─ service.runbook_index() ──► Chroma metadata (startup check + sidebar list)
```

### The page
- **Sidebar.** Setup selector (`chain`, `graph`, `graph_routing`: the same three setups as the evals), **Clear chat**, and an About section listing the runbooks found in Chroma.
- **Chat.** Questions go in the box at the bottom. A note says each question is answered on its own (no conversation memory yet).
- **Each answer** shows:
  - The answer as markdown. A refusal ("I don't have a runbook for this") is shown as a yellow warning instead.
  - **Sources**: title, filename, section and chunk text of every chunk the model was given.
  - **Behind the scenes**: setup, routed topic, search queries (original + rewritten), retries, kept vs filtered chunks, latency, and an **Open trace in LangSmith** button.
  - **Feedback**: 👍/👎, then an optional comment and **Send feedback**. After sending, the thumbs are locked and a confirmation is shown.

### Run IDs: how the trace link and feedback find the right run
`service.answer` doesn't pass one, so `ask_graph` / `ask_with_sources` create a new `uuid4` and pass it as `config={"run_id": ...}` to `.invoke()`. LangChain uses it as the ID of the **root run** in LangSmith, so the ID is known up front: no need to read it back from callbacks.
- **Trace link:** `{LANGSMITH_WEB_URL}/o/<org id>/projects/p/<project id>/r/<run id>?poll=true`. The org and project IDs come from one `read_project` call, cached. `poll=true` makes the page wait if the run is still uploading. If the lookup fails, the run ID is shown instead.
- **Feedback:** `client.create_feedback(run_id=..., trace_id=..., key="user_rating", score=1 or 0, comment=...)`. Passing `trace_id` (the same ID, since it's the root run) lets LangSmith accept feedback even if the run hasn't finished uploading.
- With tracing off, there is no link and no feedback; a short caption says why.

### Startup and errors
- **No `.env` / no `OPENAI_API_KEY`:** a message pointing to `.env.example`, no crash.
- **No search database:** if `chroma_db/` is missing or empty, the page shows the ingest command and stops. The folder is checked *before* opening Chroma, because opening a missing folder would silently create an empty one.
- **OpenAI errors** (rate limit, timeout, connection, bad key, other API errors) become a short message in the chat. The full traceback goes to the terminal where Streamlit runs.

---

## New concepts

- **Reruns.** Streamlit runs `app.py` top to bottom on **every** interaction: sending a message, clicking a button, changing the setup. There are no event handlers; the page is simply rebuilt from the current state. That's why the chat history must be stored somewhere that survives a rerun.
- **`st.session_state`.** A dict that survives reruns, one per browser tab. The chat history is `st.session_state.messages`: user messages, plus assistant messages holding the full `AnswerResult` and a `feedback_sent` flag. Refreshing the page or **Clear chat** empties it.
- **`st.cache_resource`.** Runs a function once per server process and shares the result across reruns and users. Used for loading each setup's chain/graph. (Our `lru_cache` factories already make these process-wide singletons; `cache_resource` makes this explicit at the UI level and shows a spinner on first load.)
- **`st.cache_data(ttl=60)`.** The same idea for plain data, with an expiry. Used for the runbook list, so a fresh ingest shows up within a minute without restarting the app.
- **Chat elements.** `st.chat_message("user" | "assistant")` draws one bubble; anything inside the `with` block goes into it. `st.chat_input` is the box pinned to the bottom; it returns the text **once**, on the rerun right after submit, and `None` otherwise.
- **Widget keys.** Every widget needs a unique, stable key so Streamlit can keep its value across reruns. Feedback widgets use the message's own id (`rating_<id>`), so each answer has its own thumbs.
- **Forms.** `st.form` batches widgets: typing a comment doesn't trigger reruns; only the submit button does.
- **`st.stop()`.** Ends the current run early. Used when the index is missing.
- **AppTest** (`streamlit.testing.v1`). Runs the page in-process without a browser. Tests can type into the chat, click buttons and read what was rendered. Combined with a faked `service`, this tests the real UI code offline.

---

## How to run

```powershell
uv run python -m runbook_assistant.ingest            # once, or after changing runbooks
uv run streamlit run src/runbook_assistant/app.py    # opens http://localhost:8501
```

Run from inside `runbook-assistant\`. Stop with `Ctrl+C` in the terminal.

### Checks
```powershell
uv run ruff check . --fix; uv run ruff format .
uv run pytest                                        # 74 passed (no API calls)
```

### What to try
1. "IIS app pool keeps stopping": an answer with sources from `iis-app-pool-crashes.md`.
2. "my website is down after we pushed": vague. Open **Behind the scenes** to see the routed topic and whether a rewrite happened.
3. "How do I restart a Kubernetes pod?": should show the yellow refusal.
4. Switch the setup to **Chain** and ask question 3 again: compare.
5. Rate an answer, then open the trace in LangSmith: the feedback appears on the run (key `user_rating`). In the project's runs list, filter by tag `ui` to see only UI runs.

---

## Notes and decisions

- **Streamlit, not React.** The whole UI is one Python file calling our existing functions: no separate frontend, API server or build step. All logic lives in `service.py`, which has no Streamlit in it. A React frontend later would only need a small FastAPI wrapper around `service`.
- **`service.py` instead of logic in `app.py`.** Keeps `app.py` to layout and state, and makes the logic unit-testable.
- **Extending, not duplicating.** `ask_graph` and `ask_with_sources` now return `run_id` and `latency_s` themselves. The evals and `ask.py` are unaffected: the new parameters are optional.
- **One list of setups.** `SETUPS` and `options_for_setup()` moved from `evals/run_evals.py` to `service.py`, so the UI and evals can't drift apart. Experiment prefixes stay in `run_evals.py`.
- **`is_refusal` moved to `rag.py`,** next to `NO_ANSWER`, because the UI and the evals both need it.
- **Question length.** `st.chat_input(max_chars=...)` would silently stop typing at the limit. Instead the app accepts the text and replies with a friendly message saying how long it was.
- **Answers are added via `st.rerun()`.** After an answer arrives, it's stored in `session_state` and the page reruns, so every message (new or old) is drawn by the same code with the same widget keys.
- **Importing `openai` exceptions** in `service.friendly_error` is only for recognising error types; all API calls still go through LangChain.
- **No conversation memory yet.** Each question is answered independently, as the on-page note says.

## Next milestone

Run the app and the remaining eval setups, then pick from the results: conversation memory (follow-up questions), streaming the answer token by token, or improving `correctness` (`retriever_k`, the answer prompt, hybrid search).

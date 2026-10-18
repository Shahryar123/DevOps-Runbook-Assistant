# Milestone 2 — Basic RAG

**Status:** Built and tested. Ingest and ask haven't been run yet.
**Goal:** Answer questions from the runbooks with a simple RAG chain: find the relevant runbook sections, then have `gpt-5.4-mini` answer using only those sections. LangGraph comes in Milestone 3.

---

## What changed

| File | Purpose |
|---|---|
| `data/runbooks/*.md` | Added YAML frontmatter (`title`, `topic`) to each runbook |
| `src/runbook_assistant/config.py` | New settings: `chunk_size`, `chunk_overlap`, `retriever_k`, `chroma_collection`, `runbook_topics` |
| `src/runbook_assistant/ingest.py` | Loads the runbooks, splits them into chunks, embeds the chunks and stores them in Chroma |
| `src/runbook_assistant/retriever.py` | Shared embeddings, the Chroma vector store and `get_retriever(k)` |
| `src/runbook_assistant/rag.py` | The LCEL chain plus `ask()` and `ask_with_sources()` |
| `scripts/ask.py` | Command-line tool: prints the answer and the chunks that were retrieved |
| `tests/test_ingest.py` | 9 tests for parsing, chunking and metadata, with no OpenAI calls |
| `pyproject.toml` / `uv.lock` | Added `pyyaml` and `langchain-text-splitters` as direct dependencies |

---

## How it works

```
INGEST (once, or whenever the runbooks change)
  data/runbooks/*.md
    -> load (one Document per file)
    -> parse frontmatter (title, topic -> metadata)
    -> split by headings (#, ##, ###)
    -> split by size (800 chars, 100 overlap)
    -> embed each chunk (OpenAI)
    -> store in Chroma (chroma_db/)

ASK (every question)
  question
    -> retriever: embed the question, find the 4 closest chunks
    -> prompt: rules + labelled chunks + question
    -> gpt-5.4-mini
    -> answer text
```

### Runbook frontmatter

```yaml
---
title: AWS EC2 Instance Unreachable
topic: networking        # deployment | networking | security | cicd
---
```

Ingest stops with a clear error if `title` is missing or `topic` isn't one of the allowed values in `config.runbook_topics`.

| Runbook | Topic |
|---|---|
| `aws-ec2-unreachable.md` | networking |
| `azure-devops-pipeline-failures.md` | cicd |
| `iis-app-pool-crashes.md` | deployment |

### Chunk metadata and IDs

Every chunk has this metadata:

```python
{"source": "aws-ec2-unreachable.md", "title": "AWS EC2 Instance Unreachable",
 "topic": "networking", "section": "Diagnosis Steps > 2. Check the network path"}
```

Each chunk's ID is `<filename>::<index>`, for example `aws-ec2-unreachable.md::4`. Ingest calls `reset_collection()` and then stores everything again, so you can run it as often as you like. You never get duplicates, and if you delete a runbook its chunks are removed too.

With the current three runbooks you get 36 chunks, and the largest is 792 characters.

### The chain (`rag.py`)

```python
retrieve = RunnableParallel(docs=retriever, question=RunnablePassthrough())
generate = RunnablePassthrough.assign(context=format_docs) | PROMPT | llm | StrOutputParser()
chain    = retrieve | RunnablePassthrough.assign(answer=generate)
```

The input is a string, and the output is `{"question", "docs", "answer"}`. `ask()` returns only `answer`. `ask_with_sources()` also returns `docs`, so `scripts/ask.py` can show you what retrieval found without searching a second time.

The prompt rules:
- Answer only from the context.
- Cite the source filename(s).
- If the context doesn't contain the answer, reply exactly **"I don't have a runbook for this"**.

---

## New concepts

- **`Document`.** LangChain's basic unit of text: `page_content` (the text) plus `metadata` (a dict). Loaders, splitters, vector stores and retrievers all pass `Document` objects to each other.
- **Document loader.** Turns a source, such as files, URLs or a database, into `Document` objects. LangChain has many ready-made loaders. For local markdown files, "read the file and make a Document" is all a loader would do, so `load_runbooks()` does exactly that. This avoids `langchain-community`, which is being retired.
- **Text splitters.** Cut documents into small chunks so that search returns precise passages rather than whole files.
  - `MarkdownHeaderTextSplitter` splits on headings and records the headings in metadata. It knows that a `#` inside a code block is not a heading.
  - `RecursiveCharacterTextSplitter` breaks long sections down to `chunk_size`, trying to split between paragraphs first, then lines, then words. `chunk_overlap` repeats a little text between neighbouring chunks so a sentence cut at a boundary still has its context.
- **Embeddings.** `OpenAIEmbeddings` turns text into a vector of 1536 numbers. Texts with similar meanings get vectors that are close together. Ingest and retrieval **must** use the same embedding model.
- **Vector store (Chroma).** Stores the text, metadata and vector of each chunk, and can find the stored vectors nearest to a query vector. Ours is saved on disk in `chroma_db/`.
- **Retriever.** A Runnable whose `.invoke(question)` returns a list of Documents. `vector_store.as_retriever(search_kwargs={"k": 4})` returns the 4 nearest chunks. The chain only knows "question in, documents out", so the search method can be replaced later (for example with hybrid search) without changing the chain.
- **Prompt template.** `ChatPromptTemplate` holds a system message and a human message with `{context}` and `{question}` placeholders. Calling it with a dict fills in the placeholders and produces the chat messages.
- **LCEL and the `|` operator.** LCEL stands for LangChain Expression Language. `a | b` creates a new Runnable that passes `a`'s output into `b`, and the result supports `.invoke()`, `.batch()` and `.stream()` like any other Runnable. You can join any number of Runnables this way.
  - `RunnableParallel(docs=..., question=...)` runs several Runnables on the **same** input and returns a dict of their results.
  - `RunnablePassthrough()` returns its input unchanged, which is how the question is carried along.
  - `RunnablePassthrough.assign(key=...)` adds a new key to the dict that flows through the chain and keeps the existing keys.
- **Output parser.** `StrOutputParser()` turns the model's `AIMessage` into a plain `str`.

---

## How to run / verify

```powershell
uv run ruff check . --fix; uv run ruff format .
uv run pytest                                   # 10 passed (1 config + 9 ingest)

uv run python -m runbook_assistant.ingest       # embeds the chunks (small OpenAI cost)
uv run python scripts/ask.py "IIS app pool keeps stopping with event 5002"
uv run python scripts/ask.py "How do I reset a Kubernetes pod?"   # should say "I don't have a runbook for this"
```

What to expect from ingest:

```
Files loaded:   3
Chunks created: 36
Chunks stored:  36
```

Run ingest a second time. You should still see `Chunks stored: 36`, which shows nothing was duplicated.

---

## Notes and decisions

- **Milestone numbering.** Milestone 1 was the project skeleton, so basic RAG is Milestone 2 and LangGraph is Milestone 3.
- **IIS → `deployment`.** None of the four topics fits app pool crashes exactly. `deployment` is the closest because the runbook is about app hosting. Change the frontmatter if you prefer another topic.
- **`section` metadata** is the `##` heading, joined with the `###` heading if there is one. Text before the first `##` gets the runbook title as its section.
- **Tests never need a key.** The ingest functions take their parameters directly and never call `get_settings()`, so CI runs them without a `.env`.
- **The retrieved chunks are listed after the answer.** That way you can tell whether a bad answer came from bad retrieval or from the model.
- **`langchain-community`** is no longer imported, but it is still listed in `pyproject.toml`.

## Next milestone

**Milestone 3 — LangGraph:** turn the chain into a graph. For example: route the question, retrieve, grade whether the chunks are relevant, then either answer or reply "I don't have a runbook for this".

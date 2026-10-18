"""Load, chunk, and embed runbooks from data/runbooks/ into Chroma.

Run: uv run python -m runbook_assistant.ingest
"""

import re
from pathlib import Path

import yaml
from langchain_core.documents import Document
from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)

from runbook_assistant.config import configure_tracing, get_settings
from runbook_assistant.retriever import get_vector_store

# A "---" block at the very start of the file, e.g. title/topic.
FRONTMATTER_RE = re.compile(r"\A---\s*\r?\n(.*?)\r?\n---\s*(?:\r?\n|\Z)", re.DOTALL)

# Markdown heading levels to split on, and the metadata key each one gets.
HEADERS_TO_SPLIT_ON = [("#", "h1"), ("##", "h2"), ("###", "h3")]


def load_runbooks(directory: Path) -> list[Document]:
    """Load every .md file in `directory` as one Document per file.

    A document loader turns a source (files, URLs, databases...) into
    LangChain `Document` objects: `page_content` (text) + `metadata` (dict).
    For plain local files that is just "read file -> Document", so we do it
    directly instead of depending on langchain-community (being sunset).
    """
    return [
        Document(
            page_content=path.read_text(encoding="utf-8"),
            metadata={"source": path.name},
        )
        for path in sorted(directory.glob("*.md"))
    ]


def parse_frontmatter(doc: Document, allowed_topics: list[str]) -> Document:
    """Move YAML frontmatter (title, topic) into metadata and strip it from the text."""
    source = doc.metadata["source"]
    match = FRONTMATTER_RE.match(doc.page_content)
    if match is None:
        raise ValueError(f"{source}: missing YAML frontmatter")

    meta = yaml.safe_load(match.group(1)) or {}
    title, topic = meta.get("title"), meta.get("topic")
    if not title:
        raise ValueError(f"{source}: frontmatter is missing 'title'")
    if topic not in allowed_topics:
        raise ValueError(
            f"{source}: topic {topic!r} must be one of {', '.join(allowed_topics)}"
        )

    return Document(
        page_content=doc.page_content[match.end() :],
        metadata={**doc.metadata, "title": str(title), "topic": topic},
    )


def section_name(header_meta: dict[str, str], fallback: str) -> str:
    """Build a readable section label like 'Diagnosis Steps > 1. Check disk'."""
    parts = [header_meta[key] for key in ("h2", "h3") if key in header_meta]
    return " > ".join(parts) or header_meta.get("h1", fallback)


def split_runbook(doc: Document, chunk_size: int, chunk_overlap: int) -> list[Document]:
    """Split one runbook into chunks: first by headings, then by size.

    Text splitters cut long documents into pieces small enough to embed and
    retrieve precisely. Splitting on headings first keeps each chunk inside
    one logical section; the size splitter then breaks up long sections,
    preferring paragraph -> line -> word boundaries.
    """
    header_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=HEADERS_TO_SPLIT_ON, strip_headers=False
    )
    size_splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size, chunk_overlap=chunk_overlap
    )

    chunks: list[Document] = []
    for section in header_splitter.split_text(doc.page_content):
        metadata = {
            "source": doc.metadata["source"],
            "title": doc.metadata["title"],
            "topic": doc.metadata["topic"],
            "section": section_name(section.metadata, doc.metadata["title"]),
        }
        chunks.extend(
            Document(page_content=piece, metadata=metadata)
            for piece in size_splitter.split_text(section.page_content)
        )
    return chunks


def chunk_ids(chunks: list[Document]) -> list[str]:
    """Deterministic IDs: '<filename>::<index within that file>'."""
    counts: dict[str, int] = {}
    ids: list[str] = []
    for chunk in chunks:
        source = chunk.metadata["source"]
        index = counts.get(source, 0)
        counts[source] = index + 1
        ids.append(f"{source}::{index}")
    return ids


def build_chunks(
    directory: Path, allowed_topics: list[str], chunk_size: int, chunk_overlap: int
) -> tuple[int, list[Document]]:
    """Load, parse, and split all runbooks. Returns (files loaded, chunks)."""
    docs = load_runbooks(directory)
    chunks = [
        chunk
        for doc in docs
        for chunk in split_runbook(
            parse_frontmatter(doc, allowed_topics), chunk_size, chunk_overlap
        )
    ]
    return len(docs), chunks


def main() -> None:
    settings = get_settings()
    configure_tracing(settings)

    file_count, chunks = build_chunks(
        settings.runbooks_dir,
        settings.runbook_topics,
        settings.chunk_size,
        settings.chunk_overlap,
    )
    print(f"Files loaded:   {file_count}")
    print(f"Chunks created: {len(chunks)}")
    if not chunks:
        print(f"No runbooks found in {settings.runbooks_dir}; nothing stored.")
        return

    # Rebuild from scratch so removed/renamed runbooks don't leave stale chunks.
    store = get_vector_store()
    store.reset_collection()
    # add_documents embeds each chunk (OpenAI call) and saves it under its ID.
    store.add_documents(chunks, ids=chunk_ids(chunks))
    print(f"Chunks stored:  {len(store.get()['ids'])}")


if __name__ == "__main__":
    main()

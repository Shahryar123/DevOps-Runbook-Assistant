"""Frontmatter parsing and chunking (moved unchanged from ingest.py, so chunks
and chunk IDs are identical to earlier milestones)."""

import re
from collections.abc import Collection

import yaml
from langchain_core.documents import Document
from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)

# A "---" block at the very start of the file, e.g. title/topic.
FRONTMATTER_RE = re.compile(r"\A---\s*\r?\n(.*?)\r?\n---\s*(?:\r?\n|\Z)", re.DOTALL)

# Markdown heading levels to split on, and the metadata key each one gets.
HEADERS_TO_SPLIT_ON = [("#", "h1"), ("##", "h2"), ("###", "h3")]


def read_frontmatter(text: str) -> tuple[dict | None, str]:
    """(frontmatter dict, body). The dict is None if there is no valid block."""
    match = FRONTMATTER_RE.match(text)
    if match is None:
        return None, text
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        return None, text
    if not isinstance(meta, dict):
        return None, text
    return meta, text[match.end() :]


def parse_frontmatter(doc: Document, allowed_topics: Collection[str]) -> Document:
    """Move YAML frontmatter (title, topic) into metadata and strip it from the text."""
    source = doc.metadata["source"]
    meta, body = read_frontmatter(doc.page_content)
    if meta is None:
        raise ValueError(f"{source}: missing YAML frontmatter")

    title, topic = meta.get("title"), meta.get("topic")
    if not title:
        raise ValueError(f"{source}: frontmatter is missing 'title'")
    if topic not in allowed_topics:
        raise ValueError(
            f"{source}: topic {topic!r} must be one of {', '.join(allowed_topics)}"
        )

    return Document(
        page_content=body,
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

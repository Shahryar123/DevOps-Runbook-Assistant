"""Chunking and metadata tests. No OpenAI calls and no .env needed."""

from pathlib import Path

import pytest
from langchain_core.documents import Document

from runbook_assistant.config import Settings
from runbook_assistant.ingest import (
    chunk_ids,
    load_runbooks,
    parse_frontmatter,
    split_runbook,
)

TOPICS: list[str] = Settings.model_fields["runbook_topics"].default
CHUNK_SIZE = 200
CHUNK_OVERLAP = 20

SAMPLE = (
    """---
title: Sample Runbook
topic: networking
---

# Sample Runbook

Intro text.

## Symptoms

Things break.

## Diagnosis Steps

### 1. Check disk

```bash
# this is a comment, not a header
df -h
```

## Resolution

"""
    + "Restart the service and confirm it is healthy. " * 20
)


def make_doc(text: str = SAMPLE) -> Document:
    return Document(page_content=text, metadata={"source": "sample.md"})


def sample_chunks() -> list[Document]:
    doc = parse_frontmatter(make_doc(), TOPICS)
    return split_runbook(doc, CHUNK_SIZE, CHUNK_OVERLAP)


def test_parse_frontmatter_moves_fields_to_metadata() -> None:
    doc = parse_frontmatter(make_doc(), TOPICS)

    assert doc.metadata == {
        "source": "sample.md",
        "title": "Sample Runbook",
        "topic": "networking",
    }
    assert not doc.page_content.lstrip().startswith("---")
    assert "topic:" not in doc.page_content


def test_parse_frontmatter_rejects_unknown_topic() -> None:
    bad = SAMPLE.replace("topic: networking", "topic: cooking")
    with pytest.raises(ValueError, match="topic"):
        parse_frontmatter(make_doc(bad), TOPICS)


def test_parse_frontmatter_requires_frontmatter() -> None:
    with pytest.raises(ValueError, match="missing YAML frontmatter"):
        parse_frontmatter(make_doc("# No frontmatter here"), TOPICS)


def test_every_chunk_has_required_metadata() -> None:
    chunks = sample_chunks()

    assert chunks
    for chunk in chunks:
        assert set(chunk.metadata) == {"source", "title", "topic", "section"}
        assert chunk.metadata["source"] == "sample.md"
        assert chunk.metadata["title"] == "Sample Runbook"
        assert chunk.metadata["topic"] == "networking"


def test_sections_follow_headings() -> None:
    sections = {chunk.metadata["section"] for chunk in sample_chunks()}

    assert sections == {
        "Sample Runbook",
        "Symptoms",
        "Diagnosis Steps > 1. Check disk",
        "Resolution",
    }


def test_hash_inside_code_block_is_not_a_heading() -> None:
    code_chunk = next(c for c in sample_chunks() if "df -h" in c.page_content)

    assert "# this is a comment" in code_chunk.page_content
    assert code_chunk.metadata["section"] == "Diagnosis Steps > 1. Check disk"


def test_long_sections_are_split_to_chunk_size() -> None:
    chunks = sample_chunks()
    resolution = [c for c in chunks if c.metadata["section"] == "Resolution"]

    assert len(resolution) > 1
    assert all(len(c.page_content) <= CHUNK_SIZE for c in chunks)


def test_chunk_ids_are_deterministic_and_unique() -> None:
    ids = chunk_ids(sample_chunks())

    assert ids == chunk_ids(sample_chunks())
    assert len(ids) == len(set(ids))
    assert ids[0] == "sample.md::0"


def test_load_runbooks_uses_filename_as_source(tmp_path: Path) -> None:
    (tmp_path / "b.md").write_text(SAMPLE, encoding="utf-8")
    (tmp_path / "a.md").write_text(SAMPLE, encoding="utf-8")
    (tmp_path / "notes.txt").write_text("ignored", encoding="utf-8")

    docs = load_runbooks(tmp_path)

    assert [d.metadata["source"] for d in docs] == ["a.md", "b.md"]

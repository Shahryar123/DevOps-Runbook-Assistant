"""The manifest: a record of what is in the vector index and how it was built.

Per file: SHA-256 content hash, chunk IDs, chunk count, ingested_at; plus a
settings fingerprint: everything that changes how text becomes vectors.

Where it lives depends on the vector store (ManifestStore):
- Chroma: chroma_db/manifest.json, next to the data it describes.
- Azure AI Search: derived from the index itself. Every chunk carries its
  source, content_hash, chunk_id, ingested_at and the index fingerprint, so
  one metadata query rebuilds the manifest. The index is the single source
  of truth: there is no separate file that could drift from it, and an
  Azure Container Apps Job (whose disk is temporary) needs no extra storage.

Content hashing: SHA-256 of the file's bytes. Same bytes -> same hash, any
change -> a different hash, so comparing hashes tells us exactly which
files changed without re-embedding anything.

Atomic write: the JSON is written to a temp file in the same folder, then
renamed over the old one (os.replace). A rename is atomic on the same
filesystem, so a crash leaves either the old manifest or the new one,
never a half-written file.
"""

import hashlib
import json
import os
import tempfile
from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ValidationError

from runbook_assistant.config import Settings
from runbook_assistant.pipeline import PIPELINE_VERSION
from runbook_assistant.pipeline.chunking import HEADERS_TO_SPLIT_ON
from runbook_assistant.vectorstores.base import VectorIndex

MANIFEST_FORMAT = 1


class FileRecord(BaseModel):
    content_hash: str
    chunk_ids: list[str]
    chunk_count: int
    ingested_at: str


class Manifest(BaseModel):
    format: int = MANIFEST_FORMAT
    fingerprint: dict[str, str | int]
    files: dict[str, FileRecord] = {}
    updated_at: str = ""

    @property
    def chunk_total(self) -> int:
        return sum(record.chunk_count for record in self.files.values())


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def fingerprint(settings: Settings) -> dict[str, str | int]:
    """Every setting that changes the stored chunks or vectors.

    If any of these differ from the manifest, old and new chunks would not be
    comparable (different boundaries, or vectors in a different embedding
    space), so the index must be rebuilt from scratch.
    """
    return {
        "embedding_model": settings.embedding_model,
        "chunk_size": settings.chunk_size,
        "chunk_overlap": settings.chunk_overlap,
        "header_levels": ",".join(level for level, _ in HEADERS_TO_SPLIT_ON),
        "splitter": "markdown-headers+recursive-character",
        "vector_store": settings.vector_store,
        "collection": (
            settings.chroma_collection
            if settings.vector_store == "chroma"
            else str(settings.azure_search_index_name)
        ),
        "pipeline_version": PIPELINE_VERSION,
    }


def fingerprint_json(settings: Settings) -> str:
    """The fingerprint as a compact string, stored on every chunk."""
    return json.dumps(fingerprint(settings), sort_keys=True, separators=(",", ":"))


def fingerprint_changes(old: dict, new: dict) -> list[str]:
    """Human-readable differences, e.g. ['chunk_size: 800 -> 600']."""
    return [
        f"{key}: {old.get(key, '(none)')} -> {new[key]}"
        for key in new
        if old.get(key) != new[key]
    ]


def load_manifest(path: Path) -> Manifest | None:
    """The manifest, or None if it is missing or unreadable (-> full rebuild)."""
    try:
        return Manifest.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, ValidationError):
        return None


def save_manifest(manifest: Manifest, path: Path) -> None:
    manifest.updated_at = now_iso()
    atomic_write_text(path, manifest.model_dump_json(indent=2))


def atomic_write_text(path: Path, text: str) -> None:
    """Write via a temp file in the same folder, then rename over the target."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_json(path: Path, data: dict) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, default=str))


# --- Where the manifest is stored -------------------------------------------------


class ManifestStore(Protocol):
    def load(self) -> Manifest | None:
        """The manifest, or None (-> full rebuild)."""
        ...

    def save(self, manifest: Manifest) -> None: ...


class JsonManifestStore:
    """A JSON file next to the local index (Chroma)."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> Manifest | None:
        return load_manifest(self.path)

    def save(self, manifest: Manifest) -> None:
        save_manifest(manifest, self.path)


class IndexManifestStore:
    """The manifest derived from chunk metadata in the index (Azure AI Search).

    save() does nothing: the chunk writes themselves are the record, so after
    a crash halfway through, the next load() shows exactly what was applied.
    """

    def __init__(
        self, open_index: Callable[[], VectorIndex], index_exists: Callable[[], bool]
    ) -> None:
        self.open_index = open_index
        self.index_exists = index_exists

    def load(self) -> Manifest | None:
        if not self.index_exists():  # don't create an index just to read it
            return None
        return manifest_from_metadata(self.open_index().list_metadata())

    def save(self, manifest: Manifest) -> None:
        return None


def _chunk_number(chunk_id: str) -> int:
    tail = chunk_id.rsplit("::", 1)[-1]
    return int(tail) if tail.isdigit() else 0


def manifest_from_metadata(metadatas: Iterable[dict]) -> Manifest | None:
    """Rebuild the manifest from chunk metadata. None if the index is empty.

    If chunks disagree on the fingerprint (or lack one), the result has an
    empty fingerprint, so the next run reports "settings changed" and rebuilds.
    """
    chunks: dict[str, list[dict]] = defaultdict(list)
    fingerprints: set[str] = set()
    for meta in metadatas:
        if meta.get("source") and meta.get("chunk_id"):
            chunks[meta["source"]].append(meta)
            fingerprints.add(meta.get("index_fingerprint", ""))
    if not chunks:
        return None

    stored = next(iter(fingerprints)) if len(fingerprints) == 1 else ""
    try:
        fingerprint_dict = json.loads(stored) if stored else {}
    except ValueError:
        fingerprint_dict = {}

    files = {}
    for source, metas in chunks.items():
        ids = sorted({m["chunk_id"] for m in metas}, key=_chunk_number)
        hashes = {m.get("content_hash", "") for m in metas}
        files[source] = FileRecord(
            # Mixed hashes (a partly applied change) -> "" -> seen as changed.
            content_hash=hashes.pop() if len(hashes) == 1 else "",
            chunk_ids=ids,
            chunk_count=len(ids),
            ingested_at=max(m.get("ingested_at", "") for m in metas),
        )
    return Manifest(fingerprint=fingerprint_dict, files=files)

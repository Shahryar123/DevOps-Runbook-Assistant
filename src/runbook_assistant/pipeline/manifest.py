"""The manifest: a record of what is in the vector index and how it was built.

Stored as chroma_db/manifest.json, next to the data it describes:
- per file: SHA-256 content hash, chunk IDs, chunk count, ingested_at;
- a settings fingerprint: everything that changes how text becomes vectors.

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
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ValidationError

from runbook_assistant.config import Settings
from runbook_assistant.pipeline import PIPELINE_VERSION
from runbook_assistant.pipeline.chunking import HEADERS_TO_SPLIT_ON

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
        "collection": settings.chroma_collection,
        "pipeline_version": PIPELINE_VERSION,
    }


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

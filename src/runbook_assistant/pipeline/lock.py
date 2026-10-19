"""A lock file so two ingests never write to the same index at once.

The local Chroma store is built for one writer: two ingests writing at the
same time could corrupt it. The lock is a file created with O_EXCL, which
fails if the file already exists, so only one run can hold it.

If an ingest crashes, the lock file stays behind. The error message shows
when it was taken, so you can delete it once you're sure no ingest is running.
"""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from runbook_assistant.pipeline.manifest import now_iso


class IngestLockedError(RuntimeError):
    pass


@contextmanager
def ingest_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        held = path.read_text(encoding="utf-8", errors="replace").strip()
        raise IngestLockedError(
            f"Another ingest is running ({held}). If you're sure none is, "
            f"delete {path} and try again."
        ) from None
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(f"pid {os.getpid()}, started {now_iso()}")
    try:
        yield
    finally:
        path.unlink(missing_ok=True)

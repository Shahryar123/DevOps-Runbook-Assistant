"""Where runbooks come from.

The rest of the pipeline only uses the DocumentSource interface, so another
source (Azure Blob, a Git repo, SharePoint, ...) can be added by implementing
two methods, without changing validation, sync or reporting.
"""

from pathlib import Path
from typing import NamedTuple, Protocol


class SourceDocument(NamedTuple):
    source_id: str  # stable name, used as chunk metadata "source" (e.g. a filename)
    content: str


class DocumentSource(Protocol):
    def list_ids(self) -> list[str]:
        """Every document currently in the source, sorted."""
        ...

    def read(self, source_id: str) -> str:
        """The document's full text."""
        ...


class LocalFolderSource:
    """Markdown files in one folder (not recursive). The filename is the ID."""

    def __init__(self, directory: Path, pattern: str = "*.md") -> None:
        self.directory = directory
        self.pattern = pattern

    def list_ids(self) -> list[str]:
        return sorted(path.name for path in self.directory.glob(self.pattern))

    def read(self, source_id: str) -> str:
        return (self.directory / source_id).read_text(encoding="utf-8")


def load_all(source: DocumentSource) -> list[SourceDocument]:
    return [SourceDocument(sid, source.read(sid)) for sid in source.list_ids()]

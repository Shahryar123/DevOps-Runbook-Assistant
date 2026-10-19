"""Validate every runbook BEFORE anything changes.

Errors stop the pipeline (nothing is embedded, written or deleted);
warnings are reported and the run continues. Every problem is collected
first, so one run shows all of them, not just the first.
"""

import re
from collections import defaultdict
from collections.abc import Sequence
from typing import NamedTuple

from pydantic import BaseModel

from runbook_assistant.config import Settings
from runbook_assistant.guardrails.commands import find_dangerous_commands
from runbook_assistant.guardrails.injection import keyword_check
from runbook_assistant.pipeline.chunking import read_frontmatter
from runbook_assistant.pipeline.sources import SourceDocument

HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)


class Issue(NamedTuple):
    source: str
    message: str


class ValidationResult(BaseModel):
    errors: list[Issue] = []
    warnings: list[Issue] = []

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_one(doc: SourceDocument, settings: Settings) -> ValidationResult:
    result = ValidationResult()

    def error(message: str) -> None:
        result.errors.append(Issue(doc.source_id, message))

    def warning(message: str) -> None:
        result.warnings.append(Issue(doc.source_id, message))

    size = len(doc.content.encode("utf-8"))
    if not doc.content.strip():
        error("file is empty")
        return result
    if size > settings.runbook_max_bytes:
        error(f"file is {size:,} bytes; the limit is {settings.runbook_max_bytes:,}")

    meta, body = read_frontmatter(doc.content)
    if meta is None:
        error("missing or invalid YAML frontmatter (--- title/topic --- block)")
    else:
        if not meta.get("title"):
            error("frontmatter is missing 'title'")
        topic = meta.get("topic")
        if topic not in settings.runbook_topics:
            allowed = ", ".join(settings.runbook_topics)
            error(f"topic {topic!r} is not one of: {allowed}")
        if not body.strip():
            error("no content after the frontmatter")

    headings = [h.lower() for h in HEADING_RE.findall(body)]
    for section in settings.runbook_expected_sections:
        if not any(h.startswith(section.lower()) for h in headings):
            warning(f"missing expected section '{section}'")

    if settings.injection_check_enabled and (
        phrase := keyword_check(doc.content, settings.injection_patterns)
    ):
        error(f'looks like instructions to the model (prompt injection): "{phrase}"')

    if settings.command_warnings_enabled:
        for found in find_dangerous_commands(doc.content, settings.dangerous_commands):
            warning(f"risky command: {found.name} ({found.severity})")

    return result


def validate(docs: Sequence[SourceDocument], settings: Settings) -> ValidationResult:
    """Validate each runbook, then the set as a whole (duplicate titles)."""
    result = ValidationResult()
    titles: dict[str, list[str]] = defaultdict(list)
    for doc in docs:
        one = validate_one(doc, settings)
        result.errors += one.errors
        result.warnings += one.warnings
        meta, _ = read_frontmatter(doc.content)
        if meta and meta.get("title"):
            titles[str(meta["title"]).strip().lower()].append(doc.source_id)

    for sources in titles.values():
        if len(sources) > 1:
            for source in sources:
                others = ", ".join(s for s in sources if s != source)
                result.errors.append(
                    Issue(source, f"duplicate title (also in {others})")
                )
    return result

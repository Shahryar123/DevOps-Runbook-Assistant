"""Dangerous command warnings: flag risky commands in answers and runbooks.

This guardrail warns rather than blocks: the command may be the right fix, but
the reader should know its impact before running it. The rules (pattern,
severity, one-line impact) are in config.DANGEROUS_COMMANDS.
"""

import re
from collections.abc import Iterable, Sequence
from functools import lru_cache
from typing import NamedTuple

from runbook_assistant.config import CommandRule

SEVERITY_ORDER = {"high": 0, "medium": 1}


class CommandWarning(NamedTuple):
    name: str
    severity: str
    warning: str


@lru_cache
def _compiled(pattern: str) -> re.Pattern:
    # MULTILINE so "$" in a pattern means end of line (e.g. DELETE without WHERE).
    return re.compile(pattern, re.IGNORECASE | re.MULTILINE)


def find_dangerous_commands(
    text: str, rules: Sequence[CommandRule]
) -> list[CommandWarning]:
    """One warning per matching rule, high severity first."""
    warnings = [
        CommandWarning(rule.name, rule.severity, rule.warning)
        for rule in rules
        if _compiled(rule.pattern).search(text)
    ]
    return sorted(warnings, key=lambda w: SEVERITY_ORDER[w.severity])


def format_warnings(warnings: Iterable[CommandWarning]) -> str:
    """Markdown block appended to the answer (empty if there are no warnings)."""
    lines = [f"- **{w.name}** ({w.severity}): {w.warning}" for w in warnings]
    if not lines:
        return ""
    return "\n\n---\n**⚠️ Caution: risky commands in this answer**\n" + "\n".join(lines)


def scan_runbooks(
    texts: Iterable[tuple[str, str]], rules: Sequence[CommandRule]
) -> dict[str, list[CommandWarning]]:
    """{source: warnings} for every (source, text) that contains a risky command.
    Used by ingest as a report; it never fails the ingest."""
    report = {source: find_dangerous_commands(text, rules) for source, text in texts}
    return {source: found for source, found in report.items() if found}

"""Secret masking: find credentials in text and replace them with a label.

Masking (vs blocking): the question still gets answered; only the secret part
is replaced, e.g. "Password=hunter2!" -> "Password=[MASKED_SECRET]".

Where it runs (defence in depth: several independent layers, so one gap
doesn't leak the secret):
1. On the question, before the graph/chain is invoked: OpenAI, the graph and
   the LangSmith trace only ever see the masked text.
2. In the LangSmith client (anonymizer): anything that still slips into a
   traced payload is masked before upload.
3. On the answer, before display: in case a runbook contains a secret.

False positives: prose like "steps to reset a password" has no "key=value"
shape, so the patterns (config.SECRET_PATTERNS) don't match it.
"""

import re
from collections import Counter
from collections.abc import Callable, Mapping
from functools import lru_cache
from typing import Any, NamedTuple

from langsmith.anonymizer import StringNodeRule, create_secret_anonymizer

from runbook_assistant.config import Settings
from runbook_assistant.guardrails.events import GuardrailEvent, guardrail_event


class MaskResult(NamedTuple):
    text: str
    found: dict[str, int]  # secret type -> count; never the values


@lru_cache
def compile_patterns(
    patterns: tuple[tuple[str, str], ...],
) -> list[tuple[str, re.Pattern]]:
    return [(name, re.compile(pattern)) for name, pattern in patterns]


def mask_secrets(text: str, patterns: Mapping[str, str], mask: str) -> MaskResult:
    """Replace every secret with `mask`. If a pattern has a `secret` group,
    only that group is replaced, so "Password=" stays readable."""
    found: Counter[str] = Counter()

    for name, regex in compile_patterns(tuple(patterns.items())):

        def replace(match: re.Match, name: str = name) -> str:
            found[name] += 1
            if "secret" not in match.re.groupindex:
                return mask
            whole = match.group(0)
            start = match.start("secret") - match.start()
            end = match.end("secret") - match.start()
            return whole[:start] + mask + whole[end:]

        text = regex.sub(replace, text)
    return MaskResult(text=text, found=dict(found))


def describe(found: Mapping[str, int]) -> str:
    """'aws_access_key x1, jwt x2': safe to log and show."""
    return ", ".join(f"{name} x{count}" for name, count in sorted(found.items()))


def mask_with_event(
    text: str, settings: Settings, where: str
) -> tuple[MaskResult, list[GuardrailEvent]]:
    """Mask `text` if enabled; return the result plus an event if anything was found."""
    if not settings.secrets_masking_enabled:
        return MaskResult(text=text, found={}), []
    result = mask_secrets(text, settings.secret_patterns, settings.secret_mask)
    if not result.found:
        return result, []
    return result, [
        guardrail_event("secrets", "masked", f"{where}: {describe(result.found)}")
    ]


def make_anonymizer(settings: Settings) -> Callable[[Any], Any]:
    """LangSmith anonymizer: LangSmith's built-in secret rules plus ours.

    Passed to langsmith.Client(anonymizer=...), it runs on run inputs,
    outputs and metadata on this machine, before anything is uploaded.
    """
    rules: list[StringNodeRule] = [
        {"pattern": regex, "replace": settings.secret_mask}
        for _, regex in compile_patterns(tuple(settings.secret_patterns.items()))
    ]
    return create_secret_anonymizer(extra_rules=rules)

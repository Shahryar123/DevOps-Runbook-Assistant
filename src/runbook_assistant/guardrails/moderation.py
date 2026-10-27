"""Input moderation: flag harmful or abusive text (hate, violence, self-harm,
...) so those requests are blocked before any retrieval or generation.

Two providers (MODERATION_PROVIDER):
- openai: OpenAI's moderation endpoint (free). Exception to the "LangChain
  only" rule: LangChain exposes moderation only as agent middleware (for
  create_agent), not as something a plain graph node can call, so this is the
  one place that calls the openai SDK directly.
- azure: Azure AI Content Safety on the Foundry resource. It returns a
  severity per category instead of a yes/no, so we block at
  MODERATION_SEVERITY_THRESHOLD and above.
"""

from functools import lru_cache
from typing import NamedTuple

import openai

from runbook_assistant.config import Settings, get_settings
from runbook_assistant.guardrails.content_safety import analyze_text


class ModerationResult(NamedTuple):
    flagged: bool
    categories: list[str]  # e.g. ["harassment", "violence"]


@lru_cache
def get_moderation_client() -> openai.OpenAI:
    return openai.OpenAI(api_key=get_settings().openai_api_key.get_secret_value())


def moderate_openai(text: str, settings: Settings) -> ModerationResult:
    response = get_moderation_client().moderations.create(
        model=settings.moderation_model, input=text
    )
    result = response.results[0]
    categories = [name for name, hit in result.categories.model_dump().items() if hit]
    return ModerationResult(flagged=result.flagged, categories=sorted(categories))


def moderate_azure(text: str, settings: Settings) -> ModerationResult:
    severities = analyze_text(text, settings)
    hits = sorted(
        f"{category.lower()} (severity {severity})"
        for category, severity in severities.items()
        if severity >= settings.moderation_severity_threshold
    )
    return ModerationResult(flagged=bool(hits), categories=hits)


def moderate(text: str) -> ModerationResult:
    settings = get_settings()
    if settings.moderation_provider == "azure":
        return moderate_azure(text, settings)
    return moderate_openai(text, settings)

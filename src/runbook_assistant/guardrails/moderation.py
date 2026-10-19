"""Input moderation with OpenAI's moderation endpoint (free to use).

It flags harmful or abusive text (harassment, hate, violence, self-harm,
illicit activity, ...) so those requests are blocked before any retrieval or
generation.

Exception to the "LangChain only" rule: LangChain exposes moderation only as
agent middleware (for create_agent), not as something a plain graph node can
call, so this module is the one place that calls the openai SDK directly.
"""

from functools import lru_cache
from typing import NamedTuple

import openai

from runbook_assistant.config import get_settings


class ModerationResult(NamedTuple):
    flagged: bool
    categories: list[str]  # e.g. ["harassment", "violence"]


@lru_cache
def get_moderation_client() -> openai.OpenAI:
    return openai.OpenAI(api_key=get_settings().openai_api_key.get_secret_value())


def moderate(text: str) -> ModerationResult:
    response = get_moderation_client().moderations.create(
        model=get_settings().moderation_model, input=text
    )
    result = response.results[0]
    categories = [name for name, hit in result.categories.model_dump().items() if hit]
    return ModerationResult(flagged=result.flagged, categories=sorted(categories))

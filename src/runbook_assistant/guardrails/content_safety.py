"""Azure AI Content Safety: the two HTTP calls behind the Azure moderation
and Prompt Shields guardrails.

Content Safety is part of the Foundry resource (kind = AIServices), so it
uses the same endpoint (FOUNDRY_ENDPOINT) and the same Entra ID token as the
models: Managed Identity in Azure, `az login` locally, no key.

Plain REST with httpx rather than an SDK: both calls are one small JSON POST,
and the Python SDK doesn't cover Prompt Shields.
"""

from collections.abc import Sequence
from typing import Any, NamedTuple

import httpx

from runbook_assistant.config import Settings
from runbook_assistant.llm import foundry_token_provider


def _post(path: str, body: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """POST to <endpoint>/contentsafety/<path>; raises on any HTTP error."""
    token = foundry_token_provider(settings.foundry_token_scope)()
    response = httpx.post(
        f"{(settings.foundry_endpoint or '').rstrip('/')}/contentsafety/{path}",
        params={"api-version": settings.content_safety_api_version},
        headers={"Authorization": f"Bearer {token}"},
        json=body,
        timeout=settings.content_safety_timeout_s,
    )
    response.raise_for_status()
    return response.json()


def analyze_text(text: str, settings: Settings) -> dict[str, int]:
    """Severity per harm category, e.g. {"Hate": 0, "Violence": 2}.

    FourSeverityLevels -> each severity is 0 (safe), 2 (low), 4 (medium) or
    6 (high).
    """
    data = _post(
        "text:analyze",
        {
            "text": text,
            "categories": settings.moderation_categories,
            "outputType": "FourSeverityLevels",
        },
        settings,
    )
    return {item["category"]: item["severity"] for item in data["categoriesAnalysis"]}


class ShieldResult(NamedTuple):
    prompt_attack: bool  # the user prompt is a jailbreak attempt
    document_attacks: list[bool]  # one per document: hidden instructions found


def shield_prompt(
    user_prompt: str, documents: Sequence[str], settings: Settings
) -> ShieldResult:
    """Prompt Shields: check a user prompt and/or documents for attacks."""
    data = _post(
        "text:shieldPrompt",
        {"userPrompt": user_prompt, "documents": list(documents)},
        settings,
    )
    return ShieldResult(
        prompt_attack=bool(data.get("userPromptAnalysis", {}).get("attackDetected")),
        document_attacks=[
            bool(doc["attackDetected"]) for doc in data.get("documentsAnalysis", [])
        ],
    )

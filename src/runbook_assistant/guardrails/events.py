"""A record of a guardrail firing, kept in the graph state and shown in the UI.

Events describe WHAT triggered (e.g. "aws_access_key x1"), never the value
itself, so they are safe to log, trace and display.
"""

import logging
from typing import TypedDict

logger = logging.getLogger("runbook_assistant.guardrails")


class GuardrailEvent(TypedDict):
    guardrail: str  # e.g. "secrets", "injection", "moderation"
    action: str  # "masked", "blocked", "warned" or "error"
    detail: str  # what triggered it; never a secret value


def guardrail_event(guardrail: str, action: str, detail: str = "") -> GuardrailEvent:
    """Create an event and log it (so every event is logged exactly once)."""
    logger.info("guardrail=%s action=%s detail=%s", guardrail, action, detail)
    return GuardrailEvent(guardrail=guardrail, action=action, detail=detail)

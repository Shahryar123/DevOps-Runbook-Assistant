"""Input guardrails: run the blocking checks on a (already masked) question.

Order matters: cheap checks run first and every check stops the pipeline as
soon as it blocks, so later (slower, paid) checks only see questions that got
that far:
1. length: instant, and protects every check after it from huge inputs;
2. moderation: one API call (OpenAI or Azure Content Safety), catches
   harmful/abusive text;
3. injection keywords: free, instant;
4. Prompt Shields: Azure's trained jailbreak classifier (if enabled);
5. injection classifier: a paid LLM call, only if nothing above blocked
   (off in Azure, where Prompt Shields does this job).

Secrets were already masked before this runs, so no check (and no API it
calls) ever sees a raw secret.

If moderation, Prompt Shields or the classifier fails (e.g. the service is down), the question is
let through ("fail open") and an "error" event is recorded: the assistant
still only answers from runbooks, so availability wins over a second opinion.
"""

import logging
from typing import NamedTuple

from runbook_assistant.config import Settings
from runbook_assistant.guardrails.events import GuardrailEvent, guardrail_event
from runbook_assistant.guardrails.injection import classify, keyword_check
from runbook_assistant.guardrails.moderation import moderate
from runbook_assistant.guardrails.prompt_shields import is_prompt_attack

logger = logging.getLogger(__name__)


class InputCheck(NamedTuple):
    blocked_reason: str | None  # a key of replies.BLOCKED_REPLIES, or None
    events: list[GuardrailEvent]


def too_long(question: str, settings: Settings) -> bool:
    return len(question) > settings.max_question_chars


def run_input_guardrails(question: str, settings: Settings) -> InputCheck:
    events: list[GuardrailEvent] = []

    if too_long(question, settings):
        detail = f"{len(question)} > {settings.max_question_chars} characters"
        return InputCheck("too_long", [guardrail_event("length", "blocked", detail)])

    if settings.moderation_enabled:
        try:
            result = moderate(question)
        except Exception:
            logger.warning("Moderation check failed", exc_info=True)
            events.append(guardrail_event("moderation", "error", "check skipped"))
        else:
            if result.flagged:
                detail = ", ".join(result.categories) or "flagged"
                events.append(guardrail_event("moderation", "blocked", detail))
                return InputCheck("moderation", events)

    if settings.injection_check_enabled:
        if phrase := keyword_check(question, settings.injection_patterns):
            events.append(
                guardrail_event("injection", "blocked", f'keyword: "{phrase}"')
            )
            return InputCheck("injection", events)
        if settings.prompt_shields_enabled:
            try:
                attack = is_prompt_attack(question, settings)
            except Exception:
                logger.warning("Prompt Shields check failed", exc_info=True)
                events.append(
                    guardrail_event("injection", "error", "prompt shields skipped")
                )
            else:
                if attack:
                    detail = "prompt shields: jailbreak attempt"
                    events.append(guardrail_event("injection", "blocked", detail))
                    return InputCheck("injection", events)
        if settings.injection_classifier_enabled:
            try:
                verdict = classify(question)
            except Exception:
                logger.warning("Injection classifier failed", exc_info=True)
                events.append(
                    guardrail_event("injection", "error", "classifier skipped")
                )
            else:
                if verdict.is_injection:
                    detail = f"classifier: {verdict.reason}"
                    events.append(guardrail_event("injection", "blocked", detail))
                    return InputCheck("injection", events)

    return InputCheck(None, events)

"""Prompt Shields (Azure AI Content Safety): a trained classifier for prompt
injection, used in two places.

- User prompt attacks (jailbreaks): "ignore your rules and ...". Runs on the
  question after the free keyword check, in place of our own LLM classifier
  (INJECTION_CLASSIFIER_ENABLED can still turn that on as well).
- Document attacks (indirect injection): instructions hidden in content the
  model will read. Runs at ingest on every new or changed runbook chunk, so a
  poisoned runbook never reaches the index.

See injection.py for the keyword check and the LLM classifier.
"""

from collections.abc import Iterator, Sequence

from runbook_assistant.config import Settings
from runbook_assistant.guardrails.content_safety import shield_prompt


def is_prompt_attack(question: str, settings: Settings) -> bool:
    return shield_prompt(question, [], settings).prompt_attack


def batches(
    texts: Sequence[str], max_documents: int, max_chars: int
) -> Iterator[list[str]]:
    """Group texts into calls of at most max_documents and max_chars in total.

    A text longer than max_chars goes in a call of its own (chunks are far
    shorter, so this doesn't happen in practice).
    """
    batch: list[str] = []
    size = 0
    for text in texts:
        if batch and (len(batch) == max_documents or size + len(text) > max_chars):
            yield batch
            batch, size = [], 0
        batch.append(text)
        size += len(text)
    if batch:
        yield batch


def scan_documents(texts: Sequence[str], settings: Settings) -> list[bool]:
    """One flag per text: True if Prompt Shields found a document attack."""
    flags: list[bool] = []
    for batch in batches(
        texts, settings.prompt_shields_max_documents, settings.prompt_shields_max_chars
    ):
        result = shield_prompt("", batch, settings).document_attacks
        if len(result) != len(batch):
            raise ValueError(
                f"Prompt Shields returned {len(result)} results for {len(batch)} documents"
            )
        flags += result
    return flags

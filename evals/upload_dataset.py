"""Create or sync the LangSmith dataset from evals/dataset.jsonl.

Run: uv run python -m evals.upload_dataset

Safe to run repeatedly: each case's `id` maps to a fixed example UUID, so
existing examples are updated, new ones created, and removed ones deleted.
"""

import json
import uuid
from collections.abc import Collection
from pathlib import Path

from langsmith import Client

from runbook_assistant.config import configure_tracing, get_settings

# Fixed namespace so the same (dataset, case id) always gives the same UUID.
EXAMPLE_NAMESPACE = uuid.UUID("5b7c2f0e-3a1d-4c8e-9f60-2d4a1b9e7c33")
REQUIRED_FIELDS = ("id", "question", "reference_answer", "expected_sources", "category")


def load_cases(path: Path, categories: Collection[str]) -> list[dict]:
    """Read and validate dataset.jsonl (one JSON object per line)."""
    cases: list[dict] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        case = json.loads(line)
        missing = [f for f in REQUIRED_FIELDS if f not in case]
        if missing:
            raise ValueError(f"line {line_no}: missing {', '.join(missing)}")
        if case["category"] not in categories:
            raise ValueError(f"line {line_no}: unknown category {case['category']!r}")
        if not isinstance(case["expected_sources"], list):
            raise TypeError(f"line {line_no}: expected_sources must be a list")
        cases.append(case)

    ids = [case["id"] for case in cases]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise ValueError(f"duplicate ids: {', '.join(sorted(duplicates))}")
    return cases


def example_id(dataset_name: str, case_id: str) -> uuid.UUID:
    return uuid.uuid5(EXAMPLE_NAMESPACE, f"{dataset_name}:{case_id}")


def to_example(case: dict, dataset_name: str) -> dict:
    """A LangSmith example: inputs (what the system gets), outputs (the
    reference used by evaluators), metadata (for filtering/grouping)."""
    return {
        "id": example_id(dataset_name, case["id"]),
        "inputs": {"question": case["question"]},
        "outputs": {
            "reference_answer": case["reference_answer"],
            "expected_sources": case["expected_sources"],
        },
        "metadata": {"case_id": case["id"], "category": case["category"]},
    }


def main() -> None:
    settings = get_settings()
    configure_tracing(settings)
    name = settings.eval_dataset_name
    examples = [
        to_example(case, name)
        for case in load_cases(settings.eval_dataset_path, settings.eval_categories)
    ]

    client = Client()
    if client.has_dataset(dataset_name=name):
        dataset = client.read_dataset(dataset_name=name)
    else:
        dataset = client.create_dataset(
            name, description="Runbook assistant eval set (from evals/dataset.jsonl)"
        )
        print(f"Created dataset {name!r}")

    existing = {ex.id for ex in client.list_examples(dataset_id=dataset.id)}
    wanted = {ex["id"] for ex in examples}
    to_create = [ex for ex in examples if ex["id"] not in existing]
    to_update = [ex for ex in examples if ex["id"] in existing]
    to_delete = list(existing - wanted)

    if to_create:
        client.create_examples(dataset_id=dataset.id, examples=to_create)
    if to_update:
        client.update_examples(dataset_id=dataset.id, updates=to_update)
    if to_delete:
        client.delete_examples(to_delete, hard_delete=True)

    print(
        f"Dataset {name!r}: {len(to_create)} created, {len(to_update)} updated, "
        f"{len(to_delete)} deleted, {len(examples)} total"
    )


if __name__ == "__main__":
    main()

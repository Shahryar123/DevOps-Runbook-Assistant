"""Foundry cloud evaluation script: rows, criteria and the evals API calls
(fake client; the real one needs Azure)."""

from types import SimpleNamespace

import pytest

from evals import foundry_evals as fe
from runbook_assistant.config import Settings
from runbook_assistant.rag import NO_ANSWER

EXAMPLE = {
    "id": "clear-01",
    "category": "clear",
    "question": "What does WAS event 5002 mean?",
    "reference_answer": "- rapid-fail protection",
    "expected_sources": ["iis-app-pool-crashes.md"],
}
OUTPUTS = {"answer": "Rapid-fail protection.", "contexts": ["a", "b"], "blocked": None}


def test_to_item_joins_the_chunks() -> None:
    assert fe.to_item(EXAMPLE, OUTPUTS) == {
        "id": "clear-01",
        "category": "clear",
        "query": "What does WAS event 5002 mean?",
        "response": "Rapid-fail protection.",
        "context": "a\n\nb",
        "ground_truth": "- rapid-fail protection",
    }


@pytest.mark.parametrize(
    ("example", "outputs", "expected"),
    [
        (EXAMPLE, OUTPUTS, True),
        ({**EXAMPLE, "expected_sources": []}, OUTPUTS, False),  # out of scope
        (EXAMPLE, {**OUTPUTS, "blocked": "injection"}, False),
        (EXAMPLE, {**OUTPUTS, "answer": NO_ANSWER}, False),
        (EXAMPLE, {**OUTPUTS, "contexts": []}, False),
    ],
)
def test_quality_only_for_real_answers(
    example: dict, outputs: dict, expected: bool
) -> None:
    assert fe.is_answered_in_scope(example, outputs) is expected


def test_judge_only_on_llm_evaluators() -> None:
    quality = fe.quality_criteria("gpt-5.4-mini")
    assert {c["evaluator_name"] for c in quality} == {
        "builtin.groundedness",
        "builtin.relevance",
        "builtin.retrieval",
        "builtin.response_completeness",
    }
    assert all(
        c["initialization_parameters"] == {"deployment_name": "gpt-5.4-mini"}
        for c in quality
    )
    assert all("initialization_parameters" not in c for c in fe.safety_criteria())


def test_every_mapped_field_is_in_the_schema() -> None:
    fields = set(fe.DATA_SOURCE_CONFIG["item_schema"]["properties"])
    for c in [*fe.quality_criteria("m"), *fe.safety_criteria()]:
        for template in c["data_mapping"].values():
            assert template.removeprefix("{{item.").removesuffix("}}") in fields


def test_metadata_fits_the_api_limit() -> None:
    settings = Settings(openai_api_key="sk-test", _env_file=None)
    metadata = fe.run_metadata("graph_routing", settings)
    assert len(metadata) <= 16
    assert all(isinstance(v, str) for v in metadata.values())


def test_answer_all_keeps_order_and_skips_failures() -> None:
    def target(inputs: dict) -> dict:
        if "boom" in inputs["question"]:
            raise RuntimeError("model down")
        return {"answer": inputs["question"].upper()}

    examples = [
        {"id": "a", "question": "one"},
        {"id": "b", "question": "boom"},
        {"id": "c", "question": "three"},
    ]
    assert fe.answer_all(examples, target, max_concurrency=2) == [
        {"answer": "ONE"},
        None,
        {"answer": "THREE"},
    ]


class FakeEvals:
    """client.evals with just enough behaviour for the script."""

    def __init__(self, existing: list[SimpleNamespace]) -> None:
        self.existing = existing
        self.created: list[dict] = []
        self.runs = SimpleNamespace(create=self.create_run, retrieve=self.retrieve)
        self.run_requests: list[dict] = []
        self.statuses = ["queued", "in_progress", "completed"]

    def list(self) -> list[SimpleNamespace]:
        return self.existing

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.created.append(kwargs)
        return SimpleNamespace(id="eval_new", name=kwargs["name"])

    def create_run(self, **kwargs: object) -> SimpleNamespace:
        self.run_requests.append(kwargs)
        return SimpleNamespace(id="run_1")

    def retrieve(self, run_id: str, eval_id: str) -> SimpleNamespace:
        return SimpleNamespace(id=run_id, status=self.statuses.pop(0))


def test_existing_evaluation_is_reused() -> None:
    evals = FakeEvals([SimpleNamespace(id="eval_old", name="runbook-quality")])
    client = SimpleNamespace(evals=evals)
    assert fe.get_or_create_eval(client, "runbook-quality", []) == "eval_old"
    assert evals.created == []


def test_run_sends_rows_inline_and_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fe.time, "sleep", lambda _: None)
    evals = FakeEvals([])
    client = SimpleNamespace(evals=evals)
    item = fe.to_item(EXAMPLE, OUTPUTS)

    run = fe.run_evaluation(
        client, "runbook-safety", fe.safety_criteria(), "r", [item], {"k": "v"}
    )

    assert run.status == "completed"
    assert evals.created[0]["name"] == "runbook-safety"
    request = evals.run_requests[0]
    assert request["eval_id"] == "eval_new"
    assert request["data_source"]["source"]["content"] == [{"item": item}]


def test_format_run_shows_counts_and_link() -> None:
    run = SimpleNamespace(
        status="completed",
        result_counts=SimpleNamespace(total=3, passed=2, failed=1, errored=0),
        per_testing_criteria_results=[
            SimpleNamespace(testing_criteria="groundedness", passed=2, failed=1)
        ],
        error=None,
        report_url="https://ai.azure.com/x",
    )
    text = fe.format_run("Quality", run)
    assert "3 total, 2 passed, 1 failed" in text
    assert "groundedness" in text
    assert "https://ai.azure.com/x" in text

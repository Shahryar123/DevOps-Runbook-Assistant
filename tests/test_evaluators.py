"""Code-evaluator and dataset tests. No OpenAI or LangSmith calls."""

import logging

import pytest

from evals import evaluators
from evals.evaluators import (
    correct_refusal,
    correctness,
    groundedness,
    is_refusal,
    retrieval_hit,
)
from evals.run_evals import TracerNoiseFilter
from evals.upload_dataset import example_id, load_cases
from runbook_assistant.config import Settings
from runbook_assistant.rag import NO_ANSWER

IN_SCOPE = {"reference_answer": "- point", "expected_sources": ["a.md"]}
MULTI = {"reference_answer": "- point", "expected_sources": ["a.md", "b.md"]}
OUT_OF_SCOPE = {"reference_answer": "none", "expected_sources": []}


def outputs(answer: str = "Do X (source: a.md)", sources: list[str] | None = None):
    return {"answer": answer, "sources": sources or [], "contexts": ["chunk"]}


@pytest.fixture
def no_judge(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test if an LLM judge is called."""

    def fail() -> None:
        raise AssertionError("judge must not be called")

    monkeypatch.setattr(evaluators, "get_judge", fail)


# --- is_refusal ---------------------------------------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        NO_ANSWER,
        NO_ANSWER + ".",
        "I DON'T HAVE A RUNBOOK FOR THIS",
        "I don’t have a runbook for this.",
        "  i don't have a runbook for this  ",
    ],
)
def test_is_refusal_matches_variants(answer: str) -> None:
    assert is_refusal(answer)


def test_is_refusal_ignores_real_answers() -> None:
    assert not is_refusal("Restart the pool with Start-WebAppPool.")
    assert not is_refusal("")


# --- retrieval_hit ---------------------------------------------------------------


def test_retrieval_hit_full() -> None:
    res = retrieval_hit(outputs(sources=["a.md", "z.md"]), IN_SCOPE)
    assert res["key"] == "retrieval_hit"
    assert res["score"] == 1.0


def test_retrieval_hit_miss() -> None:
    res = retrieval_hit(outputs(sources=["z.md"]), IN_SCOPE)
    assert res["score"] == 0.0
    assert "missing: a.md" in res["comment"]


def test_retrieval_hit_partial_multi_runbook() -> None:
    assert retrieval_hit(outputs(sources=["b.md"]), MULTI)["score"] == 0.5


def test_retrieval_hit_skipped_for_out_of_scope() -> None:
    assert retrieval_hit(outputs(sources=["a.md"]), OUT_OF_SCOPE)["score"] is None


# --- correct_refusal -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "reference", "expected"),
    [
        (NO_ANSWER, OUT_OF_SCOPE, 1.0),  # correctly refused
        ("Do X", OUT_OF_SCOPE, 0.0),  # answered something it has no runbook for
        ("Do X", IN_SCOPE, 1.0),  # answered an in-scope question
        (NO_ANSWER, IN_SCOPE, 0.0),  # wrongly refused
    ],
)
def test_correct_refusal(answer: str, reference: dict, expected: float) -> None:
    res = correct_refusal(outputs(answer), reference)
    assert res["key"] == "correct_refusal"
    assert res["score"] == expected


# --- LLM-judge shortcuts (must not call the judge) -----------------------------


def test_correctness_skipped_for_out_of_scope(no_judge: None) -> None:
    res = correctness({"question": "q"}, outputs(), OUT_OF_SCOPE)
    assert res["score"] is None


def test_correctness_zero_when_in_scope_refused(no_judge: None) -> None:
    res = correctness({"question": "q"}, outputs(NO_ANSWER), IN_SCOPE)
    assert res["score"] == 0.0


def test_groundedness_skipped_for_refusal(no_judge: None) -> None:
    assert groundedness(outputs(NO_ANSWER))["score"] is None


def test_groundedness_zero_without_context(no_judge: None) -> None:
    res = groundedness({"answer": "Do X", "sources": [], "contexts": []})
    assert res["score"] == 0.0


# --- Failed target runs -------------------------------------------------------------


@pytest.mark.parametrize("failed_outputs", [None, {}, {"error": "boom"}])
def test_failed_runs_are_skipped_not_scored(
    no_judge: None, failed_outputs: dict | None
) -> None:
    question = {"question": "q"}
    for res in (
        retrieval_hit(failed_outputs, IN_SCOPE),
        correct_refusal(failed_outputs, IN_SCOPE),
        correct_refusal(failed_outputs, OUT_OF_SCOPE),
        correctness(question, failed_outputs, IN_SCOPE),
        groundedness(failed_outputs),
    ):
        assert res["score"] is None, res["key"]
        assert "failed" in res["comment"]


# --- Tracer noise filter ------------------------------------------------------------


def test_tracer_noise_filter_hides_only_tracer_bookkeeping() -> None:
    noise = TracerNoiseFilter()

    def record(msg: str, *args: object) -> logging.LogRecord:
        return logging.LogRecord("x", logging.WARNING, "", 0, msg, args, None)

    hidden = record(
        "Error in %s.%s callback: %s",
        "LangChainTracer",
        "on_llm_end",
        "TracerException('No indexed run ID 01a1.')",
    )
    real_error = record("Error in %s callback: %s", "Foo", "ValueError('boom')")

    assert noise.filter(hidden) is False
    assert noise.filter(real_error) is True
    assert noise.count == 1


# --- dataset.jsonl --------------------------------------------------------------


def test_dataset_is_valid_and_points_at_real_runbooks() -> None:
    defaults = Settings.model_fields
    cases = load_cases(
        defaults["eval_dataset_path"].default, defaults["eval_categories"].default
    )
    runbooks = {p.name for p in defaults["runbooks_dir"].default.glob("*.md")}

    assert len(cases) >= 20
    for case in cases:
        assert set(case["expected_sources"]) <= runbooks, case["id"]
        if case["category"] == "out_of_scope":
            assert case["expected_sources"] == [], case["id"]
        elif case["category"] == "multi_runbook":
            assert len(case["expected_sources"]) >= 2, case["id"]
        else:
            assert len(case["expected_sources"]) == 1, case["id"]


def test_example_ids_are_stable() -> None:
    assert example_id("ds", "clear-01") == example_id("ds", "clear-01")
    assert example_id("ds", "clear-01") != example_id("ds", "clear-02")

"""Run Microsoft Foundry cloud evaluations for one system setup.

Run (`az login` first; needs the optional foundry-evals dependency group):
  uv run --group foundry-evals python -m evals.foundry_evals --setup graph_routing

1. Answer every question in evals/dataset.jsonl locally with the chosen setup
   (the same target function as the LangSmith experiments in run_evals.py).
2. Send the answers to two Foundry evaluations as inline data:
   - quality: in-scope questions that got a real answer. Groundedness,
     relevance, retrieval and response completeness, judged by
     FOUNDRY_EVAL_MODEL (a deployment in the Foundry project).
   - safety: every question, including the adversarial ones. Violence,
     hate/unfairness, sexual, self-harm and indirect attack, scored by
     Microsoft's hosted safety models.
3. Wait for both runs, print pass/fail counts and the portal report links.

Each evaluation is created once (looked up by name) and reused, so its runs
pile up under it on the project's Evaluation page and can be compared.
LangSmith stays the place for everyday experiments; this adds Microsoft's
built-in evaluators and a record inside the Foundry project.
"""

import argparse
import json
import time
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals.run_evals import experiment_prefix, make_target
from runbook_assistant.config import Settings, configure_tracing, get_settings
from runbook_assistant.rag import is_refusal
from runbook_assistant.service import SETUPS, options_for_setup

FINISHED = ("completed", "failed", "canceled")

# The fields each item sends; evaluators read them through data_mapping.
DATA_SOURCE_CONFIG: dict[str, Any] = {
    "type": "custom",
    "item_schema": {
        "type": "object",
        "properties": {
            name: {"type": "string"}
            for name in (
                "id",
                "category",
                "query",
                "response",
                "context",
                "ground_truth",
            )
        },
        "required": ["query", "response"],
    },
}

QUERY = "{{item.query}}"
RESPONSE = "{{item.response}}"
CONTEXT = "{{item.context}}"
GROUND_TRUTH = "{{item.ground_truth}}"


def criterion(
    name: str, mapping: dict[str, str], judge: str | None = None
) -> dict[str, Any]:
    """One built-in Foundry evaluator ("testing criterion")."""
    result: dict[str, Any] = {
        "type": "azure_ai_evaluator",
        "name": name,
        "evaluator_name": f"builtin.{name}",
        "data_mapping": mapping,
    }
    if judge:  # LLM-as-judge evaluators need a model deployment
        result["initialization_parameters"] = {"deployment_name": judge}
    return result


def quality_criteria(judge: str) -> list[dict[str, Any]]:
    """RAG quality, scored 1-5 (pass at >= 3) by the judge model."""
    return [
        criterion(
            "groundedness",
            {"query": QUERY, "response": RESPONSE, "context": CONTEXT},
            judge,
        ),
        criterion("relevance", {"query": QUERY, "response": RESPONSE}, judge),
        criterion("retrieval", {"query": QUERY, "context": CONTEXT}, judge),
        criterion(
            "response_completeness",
            {"response": RESPONSE, "ground_truth": GROUND_TRUTH},
            judge,
        ),
    ]


def safety_criteria() -> list[dict[str, Any]]:
    """Harm in the answer (severity 0-7, pass at <= 3) and whether the answer
    fell for injected instructions. Hosted models, no judge deployment."""
    mapping = {"query": QUERY, "response": RESPONSE}
    names = ("violence", "hate_unfairness", "sexual", "self_harm", "indirect_attack")
    return [criterion(name, mapping) for name in names]


def load_examples(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as file:
        return [json.loads(line) for line in file if line.strip()]


def to_item(example: dict[str, Any], outputs: dict[str, Any]) -> dict[str, str]:
    """One evaluation row: the question, our answer and the chunks it used."""
    return {
        "id": example["id"],
        "category": example["category"],
        "query": example["question"],
        "response": outputs["answer"],
        "context": "\n\n".join(outputs["contexts"]),
        "ground_truth": example.get("reference_answer", ""),
    }


def is_answered_in_scope(example: dict[str, Any], outputs: dict[str, Any]) -> bool:
    """Quality only makes sense for a real answer to an in-scope question."""
    return (
        bool(example.get("expected_sources"))
        and not outputs.get("blocked")
        and not is_refusal(outputs["answer"])
        and bool(outputs["contexts"])
    )


def answer_all(
    examples: Sequence[dict[str, Any]],
    target: Callable[[dict], dict],
    max_concurrency: int,
) -> list[dict[str, Any] | None]:
    """Run the target on every question (in parallel, order kept).
    None where the run raised, so one failure doesn't lose the others."""

    def answer(example: dict[str, Any]) -> dict[str, Any] | None:
        try:
            return target({"question": example["question"]})
        except Exception as exc:  # noqa: BLE001 - reported, then skipped
            print(f"  {example['id']} failed: {type(exc).__name__}: {exc}")
            return None

    with ThreadPoolExecutor(max_workers=max_concurrency) as pool:
        return list(pool.map(answer, examples))


def run_metadata(setup: str, settings: Settings) -> dict[str, str]:
    """Shown on the run in the portal (the API allows at most 16 string values)."""
    return {
        "setup": setup,
        "answer_model": settings.answer_model,
        "judge_model": settings.foundry_eval_model,
        "embedding_model": settings.embedding_model,
        "llm_provider": settings.llm_provider,
        "vector_store": settings.vector_store,
        "retrieval_mode": settings.retrieval_mode,
        "retriever_k": str(settings.retriever_k),
        "moderation_provider": settings.moderation_provider,
        "prompt_shields": str(settings.prompt_shields_enabled),
        "dataset": settings.eval_dataset_path.name,
    }


def inline_source(items: Iterable[dict[str, str]]) -> dict[str, Any]:
    """Rows sent with the run itself: no dataset upload, no storage account."""
    return {
        "type": "jsonl",
        "source": {
            "type": "file_content",
            "content": [{"item": item} for item in items],
        },
    }


def get_or_create_eval(client: Any, name: str, criteria: list[dict[str, Any]]) -> str:
    """The evaluation's ID: reuse the one with this name, or create it.

    An evaluation's criteria are fixed when it's created. If you change the
    criteria, change FOUNDRY_EVAL_*_NAME too, so the runs stay comparable.
    """
    for existing in client.evals.list():
        if existing.name == name:
            return existing.id
    created = client.evals.create(
        name=name, data_source_config=DATA_SOURCE_CONFIG, testing_criteria=criteria
    )
    return created.id


def wait_for(client: Any, eval_id: str, run_id: str, poll_s: float = 10.0) -> Any:
    while True:
        run = client.evals.runs.retrieve(run_id=run_id, eval_id=eval_id)
        if run.status in FINISHED:
            return run
        print(f"  {run.status} ...")
        time.sleep(poll_s)


def format_run(label: str, run: Any) -> str:
    lines = [f"\n{label}: {run.status}"]
    counts = run.result_counts
    if counts:
        lines.append(
            f"  rows: {counts.total} total, {counts.passed} passed, "
            f"{counts.failed} failed, {counts.errored} errored"
        )
    for result in run.per_testing_criteria_results or []:
        lines.append(
            f"  {result.testing_criteria:<24} passed {result.passed:>3}  "
            f"failed {result.failed:>3}"
        )
    if run.error:
        lines.append(f"  error: {run.error}")
    lines.append(f"  report: {run.report_url}")
    return "\n".join(lines)


def run_evaluation(
    client: Any,
    eval_name: str,
    criteria: list[dict[str, Any]],
    run_name: str,
    items: list[dict[str, str]],
    metadata: dict[str, str],
) -> Any:
    eval_id = get_or_create_eval(client, eval_name, criteria)
    run = client.evals.runs.create(
        eval_id=eval_id,
        name=run_name,
        metadata=metadata,
        data_source=inline_source(items),
    )
    print(f"Started {eval_name} run {run.id} ({len(items)} rows)")
    return wait_for(client, eval_id, run.id)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Foundry cloud evaluations.")
    parser.add_argument("--setup", choices=SETUPS, default="graph_routing")
    parser.add_argument(
        "--no-safety", action="store_true", help="skip the safety evaluation"
    )
    args = parser.parse_args()

    settings = get_settings()
    if not settings.foundry_project_endpoint:
        raise SystemExit(
            "Set FOUNDRY_PROJECT_ENDPOINT (terraform output foundry_project_endpoint)."
        )
    configure_tracing(settings)
    options = options_for_setup(args.setup, settings)

    examples = load_examples(settings.eval_dataset_path)
    print(f"Answering {len(examples)} questions with setup {args.setup} ...")
    outputs = answer_all(examples, make_target(options), settings.eval_max_concurrency)
    answered = [
        (ex, out) for ex, out in zip(examples, outputs, strict=True) if out is not None
    ]
    if len(answered) < len(examples):
        print(f"WARNING: {len(examples) - len(answered)} questions failed; not sent.")
    all_items = [to_item(ex, out) for ex, out in answered]
    quality_items = [
        to_item(ex, out) for ex, out in answered if is_answered_in_scope(ex, out)
    ]

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    run_name = f"{experiment_prefix(args.setup, options, settings)}-{stamp}"
    metadata = run_metadata(args.setup, settings)

    # Imported here so the rest of the module (and its tests) work without
    # the optional foundry-evals dependency group.
    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential

    with (
        DefaultAzureCredential() as credential,
        AIProjectClient(
            endpoint=settings.foundry_project_endpoint, credential=credential
        ) as project,
        project.get_openai_client() as client,
    ):
        quality = run_evaluation(
            client,
            settings.foundry_eval_quality_name,
            quality_criteria(settings.foundry_eval_model),
            run_name,
            quality_items,
            metadata,
        )
        print(format_run("Quality", quality))
        if settings.foundry_eval_safety and not args.no_safety:
            safety = run_evaluation(
                client,
                settings.foundry_eval_safety_name,
                safety_criteria(),
                run_name,
                all_items,
                metadata,
            )
            print(format_run("Safety", safety))


if __name__ == "__main__":
    main()

"""Run a LangSmith experiment for one system setup.

Run: uv run python -m evals.run_evals --setup chain | graph | graph_routing
"""

import argparse
import logging
from collections import defaultdict
from collections.abc import Callable, Iterable

from langchain_core.documents import Document
from langsmith import evaluate

from evals.evaluators import ALL_EVALUATORS
from runbook_assistant.config import Settings, configure_tracing, get_settings
from runbook_assistant.graph.build import GraphOptions, ask_graph
from runbook_assistant.rag import ask_with_sources
from runbook_assistant.service import SETUPS, options_for_setup
from runbook_assistant.tracing import get_langsmith_client

# Experiment name prefix for each setup in service.SETUPS.
EXPERIMENT_PREFIXES: dict[str, str] = {
    "chain": "chain-baseline",
    "graph": "graph-no-routing",
    "graph_routing": "graph-routing",
}

# Guardrail flags, and the experiment-name suffix used when one is switched off.
GUARDRAIL_FLAGS: dict[str, str] = {
    "secrets_masking_enabled": "nomask",
    "command_warnings_enabled": "nowarn",
    "injection_check_enabled": "noinj",
    "injection_classifier_enabled": "noclf",
    "moderation_enabled": "nomod",
    "off_topic_enabled": "nooff",
}


class TracerNoiseFilter(logging.Filter):
    """Hide and count LangSmith tracer bookkeeping warnings.

    Under evaluate()'s parallel threads, the grader's parallel .batch() calls
    make LangChain's tracer log "No indexed run ID ..." when a call finishes:
    the tracer can't match the end event to its start. It only affects how
    those grader calls appear in the LangSmith trace tree; answers and scores
    are unaffected. All other warnings still print.
    """

    MESSAGE = "No indexed run ID"

    def __init__(self) -> None:
        super().__init__()
        self.count = 0

    def filter(self, record: logging.LogRecord) -> bool:
        if self.MESSAGE in record.getMessage():
            self.count += 1
            return False
        return True


def experiment_prefix(
    setup: str, options: GraphOptions | None, settings: Settings
) -> str:
    """e.g. graph-routing, or graph-routing-nograde-r0-nomod for non-default flags."""
    prefix = EXPERIMENT_PREFIXES[setup]
    if not settings.secrets_masking_enabled:
        prefix += "-nomask"  # the only guardrail the chain has
    if options is None:
        return prefix
    if not options.grading_enabled:
        prefix += "-nograde"
    if options.max_retries != Settings.model_fields["max_retries"].default:
        prefix += f"-r{options.max_retries}"
    for flag, suffix in GUARDRAIL_FLAGS.items():
        if flag != "secrets_masking_enabled" and not getattr(settings, flag):
            prefix += f"-{suffix}"
    return prefix


def experiment_metadata(
    setup: str, options: GraphOptions | None, settings: Settings
) -> dict:
    """Everything needed to reproduce the experiment (no secrets)."""
    return {
        "setup": setup,
        "answer_model": settings.answer_model,
        "router_model": settings.router_model,
        "grader_model": settings.grader_model,
        "rewrite_model": settings.rewrite_model,
        "judge_model": settings.judge_model,
        "embedding_model": settings.embedding_model,
        "retriever_k": settings.retriever_k,
        "chunk_size": settings.chunk_size,
        "chunk_overlap": settings.chunk_overlap,
        "routing_enabled": bool(options and options.routing_enabled),
        "grading_enabled": bool(options and options.grading_enabled),
        "max_retries": options.max_retries if options else 0,
        "dataset": settings.eval_dataset_name,
        "vector_store": settings.vector_store,
        "retrieval_mode": settings.retrieval_mode,
        # The chain only has secret masking; the graph has every guardrail.
        **{flag: getattr(settings, flag) for flag in GUARDRAIL_FLAGS},
    }


def to_outputs(
    answer: str,
    docs: list[Document],
    question_sent: str,
    command_warnings: list[str] | None = None,
    blocked: str | None = None,
) -> dict:
    """Common output shape for both setups: what the model saw and said, plus
    what the guardrails did.

    `answer` is the model's answer WITHOUT the appended command warnings, so
    the judges grade the model's text, not our fixed warning lines.
    """
    return {
        "answer": answer,
        "sources": sorted({doc.metadata["source"] for doc in docs}),
        "contexts": [doc.page_content for doc in docs],
        "question_sent": question_sent,
        "command_warnings": command_warnings or [],
        "blocked": blocked,
    }


def blocked_by(result: dict) -> str | None:
    """Why the graph stopped early: an input guardrail, or the off-topic route."""
    if result["blocked_reason"]:
        return result["blocked_reason"]
    if any(e["guardrail"] == "off_topic" for e in result["guardrail_events"]):
        return "off_topic"
    return None


def make_target(options: GraphOptions | None) -> Callable[[dict], dict]:
    """The function LangSmith calls once per example: inputs -> outputs."""
    if options is None:

        def run_chain(inputs: dict) -> dict:
            result = ask_with_sources(inputs["question"])
            return to_outputs(result["answer"], result["docs"], result["question_sent"])

        return run_chain

    def run_graph(inputs: dict) -> dict:
        result = ask_graph(inputs["question"], options)
        return to_outputs(
            result["answer_body"],
            result["kept"],
            result["question_sent"],
            [w.name for w in result["command_warnings"]],
            blocked_by(result),
        )

    return run_graph


def summarize(results: Iterable[dict]) -> dict[str, dict[str, list[float]]]:
    """Scores grouped as {evaluator: {category or "ALL": [scores]}}.

    `results` is what evaluate() returns: one row per example with the
    example, the run, and its evaluation results.
    """
    scores: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for row in results:
        category = (row["example"].metadata or {}).get("category", "unknown")
        for res in row["evaluation_results"]["results"]:
            if res.score is None:
                continue
            scores[res.key]["ALL"].append(float(res.score))
            scores[res.key][category].append(float(res.score))
    return scores


def failed_cases(results: Iterable[dict]) -> list[str]:
    """IDs of examples whose target run raised an error."""
    return [
        (row["example"].metadata or {}).get("case_id", str(row["example"].id))
        for row in results
        if row["run"].error
    ]


def print_summary(
    scores: dict[str, dict[str, list[float]]], categories: list[str]
) -> None:
    columns = ["ALL", *categories]
    print(f"\n{'evaluator':<17}" + "".join(f"{c:>16}" for c in columns))
    for key in sorted(scores):
        cells = []
        for column in columns:
            values = scores[key].get(column, [])
            cell = (
                f"{sum(values) / len(values):.2f} (n={len(values)})" if values else "-"
            )
            cells.append(f"{cell:>16}")
        print(f"{key:<17}" + "".join(cells))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a LangSmith experiment.")
    parser.add_argument("--setup", choices=SETUPS, required=True)
    args = parser.parse_args()

    settings = get_settings()
    configure_tracing(settings)
    options = options_for_setup(args.setup, settings)

    tracer_noise = TracerNoiseFilter()
    logging.getLogger("langchain_core.callbacks.manager").addFilter(tracer_noise)

    results = evaluate(
        make_target(options),
        data=settings.eval_dataset_name,
        evaluators=ALL_EVALUATORS,
        experiment_prefix=experiment_prefix(args.setup, options, settings),
        metadata=experiment_metadata(args.setup, options, settings),
        max_concurrency=settings.eval_max_concurrency,
        # The client with the secret-masking anonymizer: the example inputs
        # contain fake secrets, and they're masked before the trace is uploaded.
        client=get_langsmith_client(),
    )

    rows = list(results)
    print(f"\nExperiment: {results.experiment_name}")
    print_summary(summarize(rows), settings.eval_categories)

    failed = failed_cases(rows)
    if failed:
        print(
            f"\nWARNING: {len(failed)}/{len(rows)} runs failed and were not scored: "
            f"{', '.join(failed)}. Fix the error and re-run before comparing."
        )
    if tracer_noise.count:
        print(
            f"\nNote: hid {tracer_noise.count} LangSmith tracing warnings "
            "('No indexed run ID'). Some grader calls may be missing from the "
            "trace tree in LangSmith; answers and scores are not affected."
        )


if __name__ == "__main__":
    main()

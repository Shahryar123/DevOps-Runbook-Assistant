"""Ingest runbooks from data/runbooks/ into Chroma: validate, sync, report.

Run:
  uv run python -m runbook_assistant.ingest                  # incremental sync
  uv run python -m runbook_assistant.ingest --dry-run        # show the plan, change nothing
  uv run python -m runbook_assistant.ingest --validate-only  # exit 0/1 (for CI)
  uv run python -m runbook_assistant.ingest --full           # force a full rebuild

In a container (the same image as the app):
  docker compose run --rm ingest
  (Azure Container Apps Job: command `python -m runbook_assistant.ingest`)

Exit codes: 0 ok, 1 validation failed or invalid configuration,
2 embedding/index write failed, 3 another ingest is running.

The pipeline itself lives in runbook_assistant.pipeline; this module only
parses arguments and wires in the real source, embeddings and vector index
(Chroma or Azure AI Search, from VECTOR_STORE).
"""

import argparse
import logging
import sys
from collections.abc import Sequence

from langchain_core.embeddings import Embeddings

from runbook_assistant.config import ConfigError, configure_tracing, get_settings
from runbook_assistant.pipeline.report import format_report, make_token_counter
from runbook_assistant.pipeline.run import (
    EXIT_VALIDATION,
    PipelineOptions,
    run_pipeline,
)
from runbook_assistant.pipeline.sources import LocalFolderSource
from runbook_assistant.retriever import get_embeddings
from runbook_assistant.vectorstores.base import VectorIndex
from runbook_assistant.vectorstores.factory import open_index


def parse_args(argv: Sequence[str] | None) -> PipelineOptions:
    parser = argparse.ArgumentParser(
        prog="python -m runbook_assistant.ingest",
        description="Validate runbooks and sync them into the vector index.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and show what would change; no embedding calls, no writes",
    )
    mode.add_argument(
        "--validate-only",
        action="store_true",
        help="only validate the runbooks; exit code 0 (ok) or 1 (errors)",
    )
    parser.add_argument(
        "--full", action="store_true", help="rebuild the whole index from scratch"
    )
    args = parser.parse_args(argv)
    return PipelineOptions(
        dry_run=args.dry_run, validate_only=args.validate_only, full=args.full
    )


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s"
    )
    options = parse_args(argv)
    try:
        settings = get_settings()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return EXIT_VALIDATION
    configure_tracing(settings)

    def index_factory(embeddings: Embeddings) -> VectorIndex:
        return open_index(settings, embeddings)

    report = run_pipeline(
        settings,
        source=LocalFolderSource(settings.runbooks_dir),
        embeddings_factory=get_embeddings,
        index_factory=index_factory,
        count_tokens=make_token_counter(settings.embedding_model),
        options=options,
    )
    print(format_report(report))
    if report.mode == "sync":
        print(f"Report saved to {settings.ingest_report_path}")
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())

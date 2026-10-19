"""Ingest runbooks from data/runbooks/ into Chroma: validate, sync, report.

Run:
  uv run python -m runbook_assistant.ingest                  # incremental sync
  uv run python -m runbook_assistant.ingest --dry-run        # show the plan, change nothing
  uv run python -m runbook_assistant.ingest --validate-only  # exit 0/1 (for CI)
  uv run python -m runbook_assistant.ingest --full           # force a full rebuild

Exit codes: 0 ok, 1 validation failed, 2 embedding/index write failed,
3 another ingest is running.

The pipeline itself lives in runbook_assistant.pipeline; this module only
parses arguments and wires in the real source, embeddings and Chroma store.
"""

import argparse
import logging
import sys
from collections.abc import Sequence

from langchain_chroma import Chroma
from langchain_core.embeddings import Embeddings

from runbook_assistant.config import Settings, configure_tracing, get_settings
from runbook_assistant.pipeline.report import format_report, make_token_counter
from runbook_assistant.pipeline.run import PipelineOptions, run_pipeline
from runbook_assistant.pipeline.sources import LocalFolderSource
from runbook_assistant.pipeline.sync import StoreFactory
from runbook_assistant.retriever import get_embeddings


def make_store_factory(settings: Settings) -> StoreFactory:
    """Open the same collection the retriever uses, with the given embeddings."""

    def open_store(embeddings: Embeddings) -> Chroma:
        return Chroma(
            collection_name=settings.chroma_collection,
            embedding_function=embeddings,
            persist_directory=str(settings.chroma_dir),
        )

    return open_store


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
    settings = get_settings()
    configure_tracing(settings)

    report = run_pipeline(
        settings,
        source=LocalFolderSource(settings.runbooks_dir),
        embeddings_factory=get_embeddings,
        store_factory=make_store_factory(settings),
        count_tokens=make_token_counter(settings.embedding_model),
        options=options,
    )
    print(format_report(report))
    if report.mode == "sync":
        print(f"Report saved to {settings.ingest_report_path}")
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())

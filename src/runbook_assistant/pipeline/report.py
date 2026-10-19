"""The ingest report: printed to the console and saved as chroma_db/ingest_report.json
(an artifact a CI job can keep)."""

import logging
from collections.abc import Callable, Sequence

from pydantic import BaseModel

from runbook_assistant.pipeline.validation import ValidationResult

logger = logging.getLogger(__name__)

TokenCounter = Callable[[Sequence[str]], tuple[int, bool]]


class IngestReport(BaseModel):
    mode: str  # "sync", "dry-run" or "validate-only"
    status: str = "ok"  # "ok", "validation_failed", "locked" or "failed"
    exit_code: int = 0
    error: str | None = None
    started_at: str
    duration_s: float = 0.0

    validation_errors: list[dict[str, str]] = []
    validation_warnings: list[dict[str, str]] = []

    full_rebuild: bool = False
    rebuild_reason: str | None = None
    new: list[str] = []
    changed: list[str] = []
    unchanged: list[str] = []
    deleted: list[str] = []

    chunks_added: int = 0  # chunks written (new + changed files)
    chunks_removed: int = 0  # chunks deleted (deleted files, stale IDs, rebuild)
    chunks_total: int | None = None  # in the index after the run
    embedding_calls: int = 0
    embedding_tokens: int = 0
    tokens_are_estimate: bool = False  # True if tiktoken wasn't available
    estimated_cost_usd: float = 0.0

    def add_validation(self, result: ValidationResult) -> None:
        self.validation_errors = [i._asdict() for i in result.errors]
        self.validation_warnings = [i._asdict() for i in result.warnings]


def make_token_counter(model: str) -> TokenCounter:
    """Count tokens like the embedding API does (tiktoken). If tiktoken can't
    load its tables (e.g. offline), fall back to ~4 characters per token."""

    def count(texts: Sequence[str]) -> tuple[int, bool]:
        try:
            import tiktoken

            encoding = tiktoken.encoding_for_model(model)
            return sum(len(encoding.encode(t)) for t in texts), False
        except Exception:  # noqa: BLE001 - any failure -> estimate
            logger.info("tiktoken unavailable; estimating tokens from characters")
            return sum(len(t) for t in texts) // 4, True

    return count


def estimate_cost(tokens: int, price_per_1m: float) -> float:
    return round(tokens / 1_000_000 * price_per_1m, 6)


def format_report(report: IngestReport) -> str:
    lines = [f"\n=== Ingest report ({report.mode}) ==="]

    lines.append(
        f"Validation: {len(report.validation_errors)} error(s), "
        f"{len(report.validation_warnings)} warning(s)"
    )
    lines += [
        f"  ERROR   {i['source']}: {i['message']}" for i in report.validation_errors
    ]
    lines += [
        f"  warning {i['source']}: {i['message']}" for i in report.validation_warnings
    ]

    if report.mode != "validate-only" and report.status != "validation_failed":
        if report.full_rebuild:
            lines.append(f"Full rebuild: {report.rebuild_reason}")
        lines.append(
            f"Files: {len(report.new)} new, {len(report.changed)} changed, "
            f"{len(report.unchanged)} unchanged, {len(report.deleted)} deleted"
        )
        for label, names in (
            ("new", report.new),
            ("changed", report.changed),
            ("deleted", report.deleted),
        ):
            lines += [f"  {label:<8}{name}" for name in names]
        if report.mode == "dry-run":
            lines.append(
                f"Chunks: {report.chunks_added} would be added, "
                f"{report.chunks_removed} would be removed"
            )
        else:
            total = (
                "" if report.chunks_total is None else f", {report.chunks_total} total"
            )
            lines.append(
                f"Chunks: {report.chunks_added} added, "
                f"{report.chunks_removed} removed{total}"
            )
        approx = "~" if report.tokens_are_estimate else ""
        lines.append(
            f"Embedding: {approx}{report.embedding_tokens:,} tokens, "
            f"est. ${report.estimated_cost_usd:.6f}"
            + (
                f", {report.embedding_calls} API call(s)"
                if report.mode == "sync"
                else ""
            )
        )

    lines.append(f"Duration: {report.duration_s:.2f} s")
    if report.error:
        lines.append(f"\n{report.status.upper()}: {report.error}")
    if (
        report.mode == "sync"
        and report.status == "ok"
        and (report.chunks_added or report.chunks_removed)
    ):
        lines.append(
            "\nIf the Streamlit app is running, restart it to load the changes."
        )
    return "\n".join(lines)

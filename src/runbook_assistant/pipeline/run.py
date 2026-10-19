"""Run the pipeline: validate -> plan -> (embed -> apply -> manifest) -> report.

Dependencies (document source, embeddings, Chroma store, token counter) are
passed in, so tests run the real pipeline with a fake embedding model and a
temporary Chroma folder.

Dry run: validate and plan exactly as a real run would, then stop before the
first side effect: no embedding calls, no Chroma writes, no manifest or
report file. It shows what WOULD happen, so you can check before paying.
"""

import logging
import time
from collections.abc import Callable
from typing import NamedTuple

from langchain_core.embeddings import Embeddings

from runbook_assistant.config import Settings
from runbook_assistant.pipeline.lock import IngestLockedError, ingest_lock
from runbook_assistant.pipeline.manifest import (
    Manifest,
    fingerprint,
    load_manifest,
    now_iso,
    save_manifest,
    write_json,
)
from runbook_assistant.pipeline.report import (
    IngestReport,
    TokenCounter,
    estimate_cost,
)
from runbook_assistant.pipeline.sources import DocumentSource, load_all
from runbook_assistant.pipeline.sync import (
    CachingEmbeddings,
    StoreFactory,
    SyncPlan,
    SyncResult,
    apply_plan,
    plan_sync,
)
from runbook_assistant.pipeline.validation import validate

logger = logging.getLogger(__name__)

EXIT_OK, EXIT_VALIDATION, EXIT_FAILED, EXIT_LOCKED = 0, 1, 2, 3


class PipelineOptions(NamedTuple):
    dry_run: bool = False
    validate_only: bool = False
    full: bool = False


class NoEmbeddings(Embeddings):
    """Used for read-only access to the store (dry runs): any call is a bug."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("dry run must not call the embedding model")

    def embed_query(self, text: str) -> list[float]:
        raise RuntimeError("dry run must not call the embedding model")


def index_count(settings: Settings, store_factory: StoreFactory) -> int | None:
    """Chunks in the index, or None if there is no index folder. Never creates it."""
    if not settings.chroma_dir.exists():
        return None
    return len(store_factory(NoEmbeddings()).get(include=[])["ids"])


def run_pipeline(
    settings: Settings,
    source: DocumentSource,
    embeddings_factory: Callable[[], Embeddings],
    store_factory: StoreFactory,
    count_tokens: TokenCounter,
    options: PipelineOptions | None = None,
) -> IngestReport:
    options = options or PipelineOptions()
    start = time.perf_counter()
    mode = (
        "validate-only"
        if options.validate_only
        else "dry-run"
        if options.dry_run
        else "sync"
    )
    report = IngestReport(mode=mode, started_at=now_iso())
    try:
        _run(
            settings,
            source,
            embeddings_factory,
            store_factory,
            count_tokens,
            options,
            report,
        )
    finally:
        report.duration_s = round(time.perf_counter() - start, 3)
        if mode == "sync":  # dry runs and validate-only write nothing
            write_json(settings.ingest_report_path, report.model_dump())
    return report


def _run(
    settings: Settings,
    source: DocumentSource,
    embeddings_factory: Callable[[], Embeddings],
    store_factory: StoreFactory,
    count_tokens: TokenCounter,
    options: PipelineOptions,
    report: IngestReport,
) -> None:
    # 1. Validate everything before any change.
    docs = load_all(source)
    validation = validate(docs, settings)
    report.add_validation(validation)
    if not validation.ok:
        report.status, report.exit_code = "validation_failed", EXIT_VALIDATION
        report.error = "validation failed; nothing was changed"
        return
    if options.validate_only:
        return

    # 2. Plan: compare the source with the manifest (and the index).
    manifest = load_manifest(settings.manifest_path)
    plan = plan_sync(
        docs,
        manifest,
        settings,
        index_count(settings, store_factory),
        ingested_at=report.started_at,
        force_full=options.full,
    )
    report.full_rebuild, report.rebuild_reason = plan.full_rebuild, plan.rebuild_reason
    report.new, report.changed = plan.new, plan.changed
    report.unchanged, report.deleted = plan.unchanged, plan.deleted
    texts = plan.texts_to_embed
    # Identical chunk texts are embedded once (see CachingEmbeddings.prefetch).
    unique_texts = list(dict.fromkeys(texts))
    report.embedding_tokens, report.tokens_are_estimate = count_tokens(unique_texts)
    report.estimated_cost_usd = estimate_cost(
        report.embedding_tokens, settings.embedding_price_per_1m_tokens
    )
    if options.dry_run:
        report.chunks_added = len(texts)
        report.chunks_removed = _planned_removals(plan, manifest)
        return
    if not plan.has_changes:
        report.chunks_total = manifest.chunk_total if manifest else 0
        return

    # 3-5. Embed, apply, save the manifest: one writer at a time.
    try:
        with ingest_lock(settings.ingest_lock_path):
            _embed_and_apply(
                settings, plan, manifest, embeddings_factory, store_factory, report
            )
    except IngestLockedError as exc:
        report.status, report.exit_code, report.error = "locked", EXIT_LOCKED, str(exc)


def _embed_and_apply(
    settings: Settings,
    plan: SyncPlan,
    manifest: Manifest | None,
    embeddings_factory: Callable[[], Embeddings],
    store_factory: StoreFactory,
    report: IngestReport,
) -> None:
    embeddings = CachingEmbeddings(embeddings_factory())
    try:
        # All network calls happen here, before the index is touched.
        embeddings.prefetch(plan.texts_to_embed, settings.embedding_batch_size)
    except Exception as exc:
        logger.exception("Embedding failed")
        report.embedding_calls = embeddings.calls
        report.status, report.exit_code = "failed", EXIT_FAILED
        report.error = (
            f"embedding failed ({type(exc).__name__}: {exc}); nothing was written, "
            "the index and manifest are unchanged"
        )
        return
    report.embedding_calls = embeddings.calls

    if plan.full_rebuild or manifest is None:
        manifest = Manifest(fingerprint=fingerprint(settings))
    store = store_factory(embeddings)
    result = SyncResult()
    try:
        apply_plan(plan, store, manifest, report.started_at, result)
    except Exception as exc:
        logger.exception("Writing to the index failed")
        report.status, report.exit_code = "failed", EXIT_FAILED
        report.error = (
            f"writing to the index failed ({type(exc).__name__}: {exc}); "
            "the manifest records only the changes that were applied"
        )
    finally:
        # Saved even after a failure: it records exactly what was applied.
        save_manifest(manifest, settings.manifest_path)
        report.chunks_added = result.chunks_added
        report.chunks_removed = result.chunks_removed
        report.chunks_total = len(store.get(include=[])["ids"])


def _planned_removals(plan: SyncPlan, manifest: Manifest | None) -> int:
    """Chunks a real run would delete (for the dry-run report)."""
    if manifest is None:
        return 0
    if plan.full_rebuild:
        return manifest.chunk_total
    removed = sum(manifest.files[s].chunk_count for s in plan.deleted)
    for fc in plan.to_embed:
        old = manifest.files.get(fc.source)
        if old:
            removed += len(set(old.chunk_ids) - set(fc.ids))
    return removed

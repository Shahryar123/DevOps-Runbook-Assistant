"""Run the pipeline: validate -> plan -> scan -> (embed -> apply -> manifest) -> report.

Scan (optional): new and changed chunks are checked by Prompt Shields for
hidden instructions before anything is embedded or written. A hit fails the
run like a validation error; a failed scan fails it like a failed embedding.

Dependencies (document source, embeddings, vector index, token counter) are
passed in, so tests run the real pipeline with a fake embedding model and a
temporary Chroma folder.

Dry run: validate and plan exactly as a real run would, then stop before the
first side effect: no embedding calls, no index writes, no manifest or
report file. It shows what WOULD happen, so you can check before paying.
"""

import logging
import time
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager, nullcontext
from typing import NamedTuple

from langchain_core.embeddings import Embeddings

from runbook_assistant.config import Settings
from runbook_assistant.pipeline.lock import IngestLockedError, ingest_lock
from runbook_assistant.pipeline.manifest import (
    IndexManifestStore,
    JsonManifestStore,
    Manifest,
    ManifestStore,
    fingerprint,
    now_iso,
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
    IndexFactory,
    SyncPlan,
    SyncResult,
    apply_plan,
    plan_sync,
)
from runbook_assistant.pipeline.validation import validate
from runbook_assistant.vectorstores.base import NoEmbeddings, VectorIndex
from runbook_assistant.vectorstores.factory import index_exists as store_exists

logger = logging.getLogger(__name__)

EXIT_OK, EXIT_VALIDATION, EXIT_FAILED, EXIT_LOCKED = 0, 1, 2, 3

# Chunk texts -> one flag per text: True = looks like an injection attack.
DocumentScanner = Callable[[Sequence[str]], list[bool]]


class PipelineOptions(NamedTuple):
    dry_run: bool = False
    validate_only: bool = False
    full: bool = False


class Index(NamedTuple):
    """How this run reaches the vector index."""

    factory: IndexFactory  # open with embeddings (for writes)
    exists: Callable[[], bool]  # checked without creating anything

    def read_only(self) -> VectorIndex:
        return self.factory(NoEmbeddings())

    def count(self) -> int | None:
        """Chunks in the index, or None if there is no index yet."""
        return self.read_only().count() if self.exists() else None


def manifest_store(settings: Settings, index: Index) -> ManifestStore:
    """Chroma: a JSON file next to the data. Azure AI Search: derived from the
    index itself (the Container Apps Job's disk doesn't survive the run)."""
    if settings.vector_store == "chroma":
        return JsonManifestStore(settings.manifest_path)
    return IndexManifestStore(index.read_only, index.exists)


def writer_lock(settings: Settings) -> AbstractContextManager:
    """Only one ingest may write at a time.

    Chroma: a lock file next to the data. Azure AI Search: the Container Apps
    Job is configured with parallelism 1, so the job itself is the lock.
    """
    if settings.vector_store == "chroma":
        return ingest_lock(settings.ingest_lock_path)
    return nullcontext()


def run_pipeline(
    settings: Settings,
    source: DocumentSource,
    embeddings_factory: Callable[[], Embeddings],
    index_factory: IndexFactory,
    count_tokens: TokenCounter,
    options: PipelineOptions | None = None,
    index_exists: Callable[[], bool] | None = None,
    scan_documents: DocumentScanner | None = None,
) -> IngestReport:
    options = options or PipelineOptions()
    index = Index(index_factory, index_exists or (lambda: store_exists(settings)))
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
            index,
            count_tokens,
            options,
            report,
            scan_documents,
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
    index: Index,
    count_tokens: TokenCounter,
    options: PipelineOptions,
    report: IngestReport,
    scan_documents: DocumentScanner | None = None,
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
    store = manifest_store(settings, index)
    chunk_count = index.count()
    manifest = store.load()
    plan = plan_sync(
        docs,
        manifest,
        settings,
        chunk_count,
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

    # 3. Scan what will be added (paid calls, so not in dry runs).
    if scan_documents is not None and not _scan_ok(plan, scan_documents, report):
        return

    # 4-6. Embed, apply, save the manifest: one writer at a time.
    try:
        with writer_lock(settings):
            _embed_and_apply(
                settings, plan, manifest, store, embeddings_factory, index, report
            )
    except IngestLockedError as exc:
        report.status, report.exit_code, report.error = "locked", EXIT_LOCKED, str(exc)


def _scan_ok(plan: SyncPlan, scan: DocumentScanner, report: IngestReport) -> bool:
    """Scan every chunk to be embedded. False (report filled in) if any runbook
    is flagged or the scan itself fails; nothing has been written either way."""
    chunks = [(fc.source, c.page_content) for fc in plan.to_embed for c in fc.chunks]
    try:
        flags = scan([text for _, text in chunks])
    except Exception as exc:
        logger.exception("Prompt Shields scan failed")
        report.status, report.exit_code = "failed", EXIT_FAILED
        report.error = (
            f"prompt shields scan failed ({type(exc).__name__}: {exc}); "
            "nothing was written"
        )
        return False

    flagged = sorted({s for (s, _), hit in zip(chunks, flags, strict=True) if hit})
    if not flagged:
        return True
    report.validation_errors += [
        {
            "source": source,
            "message": "Prompt Shields: contains instructions to the model "
            "(document attack)",
        }
        for source in flagged
    ]
    report.status, report.exit_code = "validation_failed", EXIT_VALIDATION
    report.error = "validation failed; nothing was changed"
    return False


def _embed_and_apply(
    settings: Settings,
    plan: SyncPlan,
    manifest: Manifest | None,
    store: ManifestStore,
    embeddings_factory: Callable[[], Embeddings],
    index: Index,
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
    target = index.factory(embeddings)
    result = SyncResult()
    try:
        apply_plan(plan, target, manifest, report.started_at, result)
    except Exception as exc:
        logger.exception("Writing to the index failed")
        report.status, report.exit_code = "failed", EXIT_FAILED
        report.error = (
            f"writing to the index failed ({type(exc).__name__}: {exc}); "
            "the manifest records only the changes that were applied"
        )
    finally:
        # Saved even after a failure: it records exactly what was applied.
        store.save(manifest)
        report.chunks_added = result.chunks_added
        report.chunks_removed = result.chunks_removed
        report.chunks_total = target.count()


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

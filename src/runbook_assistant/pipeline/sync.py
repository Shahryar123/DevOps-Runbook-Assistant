"""Incremental sync: bring the vector index in line with the source.

Incremental sync: instead of re-embedding everything on every run, compare
each file's content hash with the manifest and only touch what changed:
  new -> chunk, embed, add          changed -> embed, upsert, drop stale IDs
  deleted -> delete its chunk IDs    unchanged -> skip (no embedding calls)
Running it twice in a row does nothing the second time: it is idempotent.

Full rebuild instead (reset the collection, embed everything) when:
- --full is requested;
- the manifest is missing or unreadable (we can't know what's in the index);
- the settings fingerprint changed: chunks built with another chunk_size,
  or vectors from another embedding model, can't be mixed with new ones;
- the index chunk count doesn't match the manifest (changed by hand).

Order: PLAN (no I/O except reading files) -> EMBED (all OpenAI calls, in
batches) -> APPLY (local Chroma writes only) -> save the manifest. If any
embedding call fails, nothing has been written yet, so the index and the
manifest still match exactly.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from runbook_assistant.config import Settings
from runbook_assistant.pipeline.chunking import (
    chunk_ids,
    parse_frontmatter,
    split_runbook,
)
from runbook_assistant.pipeline.manifest import (
    FileRecord,
    Manifest,
    content_hash,
    fingerprint,
    fingerprint_changes,
)
from runbook_assistant.pipeline.sources import SourceDocument

StoreFactory = Callable[[Embeddings], Chroma]


@dataclass
class FileChunks:
    source: str
    content_hash: str
    chunks: list[Document]
    ids: list[str]


@dataclass
class SyncPlan:
    full_rebuild: bool
    rebuild_reason: str | None
    new: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    to_embed: list[FileChunks] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return self.full_rebuild or bool(self.new or self.changed or self.deleted)

    @property
    def texts_to_embed(self) -> list[str]:
        return [chunk.page_content for fc in self.to_embed for chunk in fc.chunks]


@dataclass
class SyncResult:
    chunks_added: int = 0
    chunks_removed: int = 0
    embedding_calls: int = 0


def rebuild_reason(
    manifest: Manifest | None,
    settings: Settings,
    index_count: int | None,
    force_full: bool,
) -> str | None:
    """Why a full rebuild is needed, or None for an incremental sync.

    `index_count` is the number of chunks in Chroma, or None if there is no index.
    """
    if force_full:
        return "--full was requested"
    if manifest is None:
        return "no readable manifest (first run, or it was deleted or corrupted)"
    if changes := fingerprint_changes(manifest.fingerprint, fingerprint(settings)):
        return "settings changed: " + "; ".join(changes)
    if index_count is None:
        return "the vector index folder is missing"
    if index_count != manifest.chunk_total:
        return (
            f"the index has {index_count} chunks but the manifest lists "
            f"{manifest.chunk_total}"
        )
    return None


def prepare_chunks(
    doc: SourceDocument, settings: Settings, ingested_at: str
) -> FileChunks:
    """Chunk one runbook and add content_hash / ingested_at to every chunk."""
    digest = content_hash(doc.content)
    parsed = parse_frontmatter(
        Document(page_content=doc.content, metadata={"source": doc.source_id}),
        settings.runbook_topics,
    )
    chunks = [
        Document(
            page_content=chunk.page_content,
            metadata={
                **chunk.metadata,
                "content_hash": digest,
                "ingested_at": ingested_at,
            },
        )
        for chunk in split_runbook(parsed, settings.chunk_size, settings.chunk_overlap)
    ]
    return FileChunks(doc.source_id, digest, chunks, chunk_ids(chunks))


def plan_sync(
    docs: Sequence[SourceDocument],
    manifest: Manifest | None,
    settings: Settings,
    index_count: int | None,
    ingested_at: str,
    force_full: bool = False,
) -> SyncPlan:
    """Decide what to do. Reads nothing but the docs; changes nothing."""
    reason = rebuild_reason(manifest, settings, index_count, force_full)
    known = manifest.files if manifest else {}
    plan = SyncPlan(full_rebuild=reason is not None, rebuild_reason=reason)

    for doc in docs:
        record = known.get(doc.source_id)
        if record is None:
            plan.new.append(doc.source_id)
        elif record.content_hash != content_hash(doc.content):
            plan.changed.append(doc.source_id)
        else:
            plan.unchanged.append(doc.source_id)
            if not plan.full_rebuild:
                continue  # skip: nothing to embed
        plan.to_embed.append(prepare_chunks(doc, settings, ingested_at))

    current = {doc.source_id for doc in docs}
    plan.deleted = sorted(source for source in known if source not in current)
    return plan


class CachingEmbeddings(Embeddings):
    """Embeds texts ahead of time (prefetch), then serves them from memory.

    The pipeline prefetches every new chunk in batches BEFORE touching Chroma.
    When Chroma's add_documents later asks for vectors, they come from the
    cache, so no network call can fail halfway through writing.
    """

    def __init__(self, base: Embeddings) -> None:
        self.base = base
        self.cache: dict[str, list[float]] = {}
        self.calls = 0

    def prefetch(self, texts: Sequence[str], batch_size: int) -> None:
        missing = list(dict.fromkeys(t for t in texts if t not in self.cache))
        for start in range(0, len(missing), batch_size):
            batch = missing[start : start + batch_size]
            self.calls += 1
            self.cache.update(zip(batch, self.base.embed_documents(batch), strict=True))

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        missing = [t for t in texts if t not in self.cache]
        if missing:
            self.prefetch(missing, batch_size=len(missing))
        return [self.cache[t] for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self.base.embed_query(text)


def apply_plan(
    plan: SyncPlan,
    store: Chroma,
    manifest: Manifest,
    ingested_at: str,
    result: SyncResult,
) -> None:
    """Write the (already embedded) plan to Chroma, updating `manifest` after
    every step so it always describes what is really in the index."""
    if plan.full_rebuild:
        result.chunks_removed += len(store.get(include=[])["ids"])
        store.reset_collection()
        manifest.files.clear()

    for source in plan.deleted:
        record = manifest.files.get(source)
        if record and record.chunk_ids:
            store.delete(ids=record.chunk_ids)
            result.chunks_removed += len(record.chunk_ids)
        manifest.files.pop(source, None)

    for fc in plan.to_embed:
        if fc.chunks:
            store.add_documents(fc.chunks, ids=fc.ids)  # upsert; vectors from cache
            result.chunks_added += len(fc.chunks)
        old = manifest.files.get(fc.source)
        stale = sorted(set(old.chunk_ids) - set(fc.ids)) if old else []
        if stale:
            store.delete(ids=stale)
            result.chunks_removed += len(stale)
        manifest.files[fc.source] = FileRecord(
            content_hash=fc.content_hash,
            chunk_ids=fc.ids,
            chunk_count=len(fc.ids),
            ingested_at=ingested_at,
        )

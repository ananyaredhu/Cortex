"""The ingestion loop: read each connector's change feed and keep the index in step with the source.

Per change type (connector-interface.md v0.2):
- upsert:           fetch, upsert the document, replace its chunks.
- acl_change:       fetch, rewrite tokens on the existing chunks without re-embedding when the content is unchanged.
- delete:           remove the chunks, keep a tombstone.
- principal_change: nothing is re-indexed; the event goes to the sink.

The sink (by default the store's outbox) also gets an acl_change event whenever an indexed document's ACL
turns out to differ from the index, so the consumer can drop what it cached for that document.

Every handler is idempotent and each store write is atomic, and the cursor is saved only after a whole batch is applied, so a crash
replays at most one batch and never skips a change. A failure (network, database, sink) stops the source
with its cursor where it was; the next run retries.
"""
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from connectors.base import Change, Connector, Document, DocumentNotFound
from connectors.ingestion.chunking import MAX_CHARS, chunk_body
from connectors.ingestion.embedding import Embedder
from connectors.ingestion.events import EventSink, IngestionEvent, OutboxSink
from connectors.ingestion.freshness import LagTracker, lag_seconds, utc_now
from connectors.ingestion.store import ChunkRow, Store, same_acl

# What a change turned into. `unchanged` means the index already matched the source.
INDEXED, ACL_REWRITTEN, DELETED, UNCHANGED, PRINCIPAL = "indexed", "acl_rewritten", "deleted", "unchanged", "principal_change"


@dataclass
class RunReport:
    actions: dict[str, Counter] = field(default_factory=dict)        # source -> action -> count
    lag: dict[str, dict[str, float]] = field(default_factory=dict)   # source -> freshness_lag_seconds summary

    def count(self, source: str, action: str) -> int:
        return self.actions.get(source, Counter())[action]

    def as_json(self) -> dict:
        return {"actions": {s: dict(c) for s, c in self.actions.items()}, "freshness_lag_seconds": self.lag}


class Ingestor:
    def __init__(self, connectors: Iterable[Connector], store: Store, embedder: Embedder, *,
                 sink: EventSink | None = None, clock: Callable[[], str] = utc_now, max_chars: int = MAX_CHARS) -> None:
        self.connectors = list(connectors)
        self.store = store
        self.embedder = embedder
        self.sink = sink or OutboxSink(store)
        self.lag = LagTracker()
        self._clock = clock
        self._max_chars = max_chars

    def run_once(self) -> RunReport:
        """Drain every connector's feed once. The first run of a source is its full crawl."""
        report = RunReport()
        for connector in self.connectors:
            report.actions[connector.source] = self.run_source(connector)
        report.lag = self.lag.summary()
        return report

    def run_source(self, connector: Connector) -> Counter:
        actions: Counter = Counter()
        cursor = self.store.get_cursor(connector.source)
        # A full crawl that starts and ends in this call also tells us what no longer exists: a crawl never
        # reports deletes, so anything indexed but not listed is removed (e.g. after a simulator reset).
        crawled: set[str] | None = set() if cursor is None else None
        while True:
            batch = connector.list_changes(cursor)
            for change in batch.changes:
                actions[self._apply(connector, change)] += 1
                self.lag.record(connector.source, lag_seconds(change.detected_at, self._clock()))
                if crawled is not None and change.doc_id is not None:
                    crawled.add(change.doc_id)
            if crawled is not None and not batch.has_more:
                for doc_id in sorted(self.store.live_doc_ids(connector.source) - crawled):
                    actions[self._delete(doc_id)] += 1
            cursor = batch.next_cursor
            self.store.set_cursor(connector.source, cursor)
            if not batch.has_more:
                return actions

    # -- one change -----------------------------------------------------------------------------
    def _apply(self, connector: Connector, change: Change) -> str:
        if change.type == "principal_change":
            self.sink.emit(IngestionEvent("principal_change", connector.source, change.detected_at, self._clock(),
                                          principal=change.principal, token=change.token))
            return PRINCIPAL
        if change.doc_id is None:
            raise ValueError(f"{change.type} change without a doc_id from {connector.source}")
        if change.type == "delete":
            return self._delete(change.doc_id)
        try:
            doc = connector.fetch(change.doc_id)
        except DocumentNotFound:
            # Gone between list_changes and fetch: the same as a delete.
            return self._delete(change.doc_id)
        return self._sync(doc, change.detected_at)

    def _delete(self, doc_id: str) -> str:
        have = self.store.indexed(doc_id)
        if have is None or have.deleted:
            return UNCHANGED
        self.store.delete_document(doc_id, self._clock())
        return DELETED

    def _sync(self, doc: Document, detected_at: str) -> str:
        """Bring the index in line with `doc`, doing the least work that is correct. Used for upsert and acl_change."""
        have = self.store.indexed(doc.doc_id)
        if (have is not None and not have.deleted and have.snapshot_hash is not None
                and not same_acl(have.snapshot_hash, have.acl_tokens, doc)):
            # Emitted before the index is written: if the write then fails, the replay emits it again
            # (at least once). The other order could lose the event.
            self.sink.emit(IngestionEvent("acl_change", doc.source, detected_at, self._clock(),
                                          doc_id=doc.doc_id, snapshot_hash=doc.acl.snapshot_hash))
        content_current = (have is not None and not have.deleted and have.version == doc.version
                           and (have.embedding_model, have.embedding_version) == (self.embedder.model, self.embedder.version))
        if content_current:
            if same_acl(have.snapshot_hash, have.acl_tokens, doc):
                return UNCHANGED
            self.store.rewrite_acl(doc, self._clock())
            return ACL_REWRITTEN
        self.store.replace_document(doc, self._chunks(doc), self._clock())
        return INDEXED

    def _chunks(self, doc: Document) -> list[ChunkRow]:
        texts = chunk_body(doc.body, self._max_chars) or ([doc.title] if doc.title.strip() else [])
        vectors = self.embedder.embed(texts)
        if len(vectors) != len(texts):
            raise RuntimeError(f"embedder returned {len(vectors)} vectors for {len(texts)} texts")
        now = self._clock()
        return [ChunkRow(f"{doc.doc_id}#{i}", doc.doc_id, i, text, list(doc.acl.tokens), doc.acl.snapshot_hash,
                         doc.version, vector, self.embedder.model, self.embedder.version, now)
                for i, (text, vector) in enumerate(zip(texts, vectors, strict=True))]

"""The ingestion loop: read each connector's change feed and keep the index in step with the source.

Per change type (connector-interface.md v0.2):
- upsert:           fetch, upsert the document, replace its chunks.
- acl_change:       fetch, rewrite tokens on the existing chunks without re-embedding when the content is unchanged.
- delete:           remove the chunks, keep a tombstone.
- principal_change: nothing is re-indexed; the event goes to the sink.

The sink (by default the store's outbox) also gets an acl_change event whenever an indexed document's ACL
turns out to differ from the index, so the consumer can drop what it cached for that document.

Every handler is idempotent and each store write is atomic, and the cursor is saved only after a whole batch is applied, so a crash
replays at most one batch and never skips a change. A failure (network, database, sink) stops that source's pass
with its cursor where it was. `run_once` (and `try_source`) contain it: the other sources carry on, and the failed
source backs off, 30 s doubling to 15 minutes with jitter, or longer when a rate limit says so (`retry_after`),
before the next pass retries the same changes. A success resets the back-off.
"""
import logging
import random
import time
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from connectors.base import Change, Connector, CursorExpired, Document, DocumentNotFound
from connectors.ingestion.chunking import MAX_CHARS, chunk_body
from connectors.ingestion.embedding import Embedder
from connectors.ingestion.events import EventSink, IngestionEvent, OutboxSink
from connectors.ingestion.freshness import METRIC, PIPELINE_METRIC, LagSample, LagTracker, SourceRun, lag_seconds, parse_ts, utc_now
from connectors.ingestion.store import ChunkRow, Indexed, Store, same_acl

log = logging.getLogger(__name__)
BACKOFF_BASE = 30.0     # seconds after the first failed pass of a source
BACKOFF_MAX = 900.0     # never more than 15 minutes between attempts, unless a rate limit asks for longer

# What a change turned into. `unchanged` means the index already matched the source.
INDEXED, ACL_REWRITTEN, DELETED, UNCHANGED, PRINCIPAL = "indexed", "acl_rewritten", "deleted", "unchanged", "principal_change"


@dataclass
class RunReport:
    actions: dict[str, Counter] = field(default_factory=dict)        # source -> action -> count
    lag: dict[str, dict[str, float]] = field(default_factory=dict)        # source -> pipeline_lag_seconds summary
    freshness: dict[str, dict[str, float]] = field(default_factory=dict)  # source -> freshness_lag_seconds summary
    errors: dict[str, str] = field(default_factory=dict)      # source -> exception class of a pass that failed now
    skipped: dict[str, float] = field(default_factory=dict)   # source -> seconds until it is tried again

    def count(self, source: str, action: str) -> int:
        return self.actions.get(source, Counter())[action]

    def as_json(self) -> dict:
        out = {"actions": {s: dict(c) for s, c in self.actions.items()}, METRIC: self.freshness, PIPELINE_METRIC: self.lag}
        if self.errors:
            out["errors"] = dict(self.errors)
        if self.skipped:
            out["backing_off_seconds"] = dict(self.skipped)
        return out


@dataclass
class SourceHealth:
    failures: int = 0                  # consecutive failed passes
    retry_at: float = 0.0              # monotonic time before which the source is not tried
    last_error: str | None = None      # exception class only: messages can hold IDs and URLs


class Ingestor:
    def __init__(self, connectors: Iterable[Connector], store: Store, embedder: Embedder, *,
                 sink: EventSink | None = None, clock: Callable[[], str] = utc_now, max_chars: int = MAX_CHARS,
                 monotonic: Callable[[], float] = time.monotonic, jitter: Callable[[], float] = lambda: random.uniform(0.8, 1.2)) -> None:
        self.connectors = list(connectors)
        self.store = store
        self.embedder = embedder
        self.sink = sink or OutboxSink(store)
        self.lag = LagTracker()         # pipeline lag of changes that altered the index, in this process
        self.freshness = LagTracker()   # freshness lag of new content found incrementally, in this process
        self._clock = clock
        self._max_chars = max_chars
        self._monotonic = monotonic
        self._jitter = jitter
        self.health: dict[str, SourceHealth] = {}

    def run_once(self) -> RunReport:
        """Drain every connector's feed once. The first run of a source is its full crawl. A source that fails is
        reported in `errors` and backs off; one still backing off is reported in `skipped`. The others carry on."""
        report = RunReport()
        for connector in self.connectors:
            wait = self.backing_off(connector.source)
            if wait > 0:
                report.skipped[connector.source] = round(wait, 1)
                continue
            actions = self.try_source(connector)
            if actions is None:
                report.errors[connector.source] = self.health[connector.source].last_error or "Exception"
            else:
                report.actions[connector.source] = actions
        report.lag = self.lag.summary()
        report.freshness = self.freshness.summary()
        return report

    def backing_off(self, source: str) -> float:
        """Seconds until `source` may be tried again; 0 when it may be tried now."""
        health = self.health.get(source)
        return max(0.0, health.retry_at - self._monotonic()) if health else 0.0

    def try_source(self, connector: Connector, trigger: str = "poll") -> Counter | None:
        """`run_source` with the failure contained: logged by exception class, recorded, and the source backs off.
        None when the pass failed or the source is still backing off."""
        source = connector.source
        if self.backing_off(source) > 0:
            return None
        health = self.health.setdefault(source, SourceHealth())
        try:
            reconnect = getattr(self.store, "reconnect_if_broken", None)
            if reconnect is not None:
                reconnect()                     # after a database restart, the old connection is dead
            actions = self.run_source(connector, trigger)
        except Exception as exc:
            health.failures += 1
            health.last_error = type(exc).__name__
            delay = min(BACKOFF_BASE * 2 ** (health.failures - 1), BACKOFF_MAX) * self._jitter()
            delay = max(delay, float(getattr(exc, "retry_after", 0) or 0))   # a rate limit's Retry-After
            health.retry_at = self._monotonic() + delay
            log.warning("%s: pass failed (%s), %d in a row; next attempt in %.0f s",
                        source, health.last_error, health.failures, delay)
            return None
        if health.failures:
            log.info("%s: recovered after %d failed passes", source, health.failures)
        self.health[source] = SourceHealth()
        return actions

    def run_source(self, connector: Connector, trigger: str = "poll") -> Counter:
        """Drain one connector's feed. `trigger` says what started the pass ("poll" or "event"); a pass that
        starts without a cursor is a "crawl". Records freshness samples and the outcome of the pass in the store."""
        started = self._clock()
        actions: Counter = Counter()
        try:
            self._drain(connector, trigger, actions)
        except Exception as exc:
            self.store.record_run(SourceRun(connector.source, started, None, type(exc).__name__, dict(actions)))
            raise
        self.store.record_run(SourceRun(connector.source, started, self._clock(), None, dict(actions)))
        return actions

    def _drain(self, connector: Connector, trigger: str, actions: Counter) -> None:
        cursor = self.store.get_cursor(connector.source)
        # A full crawl that starts and ends in this call also tells us what no longer exists: a crawl never
        # reports deletes, so anything indexed but not listed is removed (e.g. after a simulator reset).
        crawled: set[str] | None = set() if cursor is None else None
        if cursor is None:
            trigger = "crawl"
        while True:
            try:
                batch = connector.list_changes(cursor)
            except CursorExpired:
                if cursor is None:
                    raise
                # The source forgot our place (a simulator restarted): crawl again, sweeping what no longer exists.
                log.warning("%s: cursor expired; starting a full crawl", connector.source)
                self.store.clear_cursor(connector.source)
                cursor, crawled, trigger = None, set(), "crawl"
                continue
            samples: list[LagSample] = []
            for change in batch.changes:
                action, source_at = self._apply(connector, change)
                actions[action] += 1
                if action != UNCHANGED:
                    samples.append(LagSample(connector.source, action, trigger, change.detected_at, self._clock(), source_at))
                if crawled is not None and change.doc_id is not None:
                    crawled.add(change.doc_id)
            if crawled is not None and not batch.has_more:
                for doc_id in sorted(self.store.live_doc_ids(connector.source) - crawled):
                    action = self._delete(doc_id)
                    actions[action] += 1
                    if action != UNCHANGED:
                        now = self._clock()
                        samples.append(LagSample(connector.source, action, trigger, now, now))
            for sample in samples:
                self.lag.record(sample.source, lag_seconds(sample.detected_at, sample.applied_at))
                if sample.source_at and sample.trigger != "crawl":
                    self.freshness.record(sample.source, lag_seconds(sample.source_at, sample.applied_at))
            self.store.record_lag(samples)
            cursor = batch.next_cursor
            self.store.set_cursor(connector.source, cursor)
            if not batch.has_more:
                return

    # -- one change -----------------------------------------------------------------------------
    def _apply(self, connector: Connector, change: Change) -> tuple[str, str | None]:
        """(what it turned into, the source's time of the edit when new content was indexed)."""
        if change.type == "principal_change":
            self.sink.emit(IngestionEvent("principal_change", connector.source, change.detected_at, self._clock(),
                                          principal=change.principal, token=change.token))
            return PRINCIPAL, None
        if change.doc_id is None:
            raise ValueError(f"{change.type} change without a doc_id from {connector.source}")
        if change.type == "delete":
            return self._delete(change.doc_id), None
        try:
            doc = connector.fetch(change.doc_id)
        except DocumentNotFound:
            # Gone between list_changes and fetch: the same as a delete.
            return self._delete(change.doc_id), None
        return self._sync(doc, change.detected_at)

    def _delete(self, doc_id: str) -> str:
        have = self.store.indexed(doc_id)
        if have is None or have.deleted:
            return UNCHANGED
        self.store.delete_document(doc_id, self._clock())
        return DELETED

    def _sync(self, doc: Document, detected_at: str) -> tuple[str, str | None]:
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
                return UNCHANGED, None
            self.store.rewrite_acl(doc, self._clock())
            return ACL_REWRITTEN, None
        self.store.replace_document(doc, self._chunks(doc), self._clock())
        return INDEXED, self._edited_at(have, doc)

    @staticmethod
    def _edited_at(have: Indexed | None, doc: Document) -> str | None:
        """The source's time of the edit this indexing reflects, for freshness; None when there is none to measure
        from. Not for a re-embed (same version), and not when the source's time did not move forward: deleting a
        Slack reply takes the thread's time back to an older message, and a Google Doc's modified time stays put
        for later edits in one editing session. Those still count in the pipeline lag."""
        if not doc.updated_at:
            return None
        if have is None or have.deleted:
            return doc.updated_at
        if have.version == doc.version:
            return None
        try:
            moved = have.updated_at is None or parse_ts(doc.updated_at) > parse_ts(have.updated_at)
        except ValueError:
            return None
        return doc.updated_at if moved else None

    def _chunks(self, doc: Document) -> list[ChunkRow]:
        texts = chunk_body(doc.body, self._max_chars) or ([doc.title] if doc.title.strip() else [])
        vectors = self.embedder.embed(texts)
        if len(vectors) != len(texts):
            raise RuntimeError(f"embedder returned {len(vectors)} vectors for {len(texts)} texts")
        now = self._clock()
        return [ChunkRow(f"{doc.doc_id}#{i}", doc.doc_id, i, text, list(doc.acl.tokens), doc.acl.snapshot_hash,
                         doc.version, vector, self.embedder.model, self.embedder.version, now)
                for i, (text, vector) in enumerate(zip(texts, vectors, strict=True))]

"""Two ways to assemble the Brain.

`fixture_runtime`: the fixture connectors over one shared `State`, A's in-memory store filled by A's ingestion with
the fake embedder, the in-memory index and audit log, the template generator. No database, no model, no network:
what the tests and C's Playwright runs use. Its `advance` and `reset` apply the scripted events (`e1`, `e2`).

`env_runtime`: Postgres, the simulators and the real Slack and Drive connectors from `.env`, bge-m3 for the query
vector, the generator the settings name. What `uvicorn brain.api.main:app` runs.
"""
import logging
from collections.abc import Callable
from dataclasses import dataclass

from brain.audit.chain import Signer
from brain.audit.service import AuditService
from brain.audit.store import AuditStore, MemoryAuditStore, PostgresAuditStore
from brain.checker import scorer_from_env
from brain.config import Settings
from brain.gateway import TemplateGenerator, generator_from_settings
from brain.pipeline.graph import Brain
from brain.policy.events import OutboxConsumer
from brain.retrieval import MemoryIndex, PostgresIndex
from connectors.ingestion import FakeEmbedder, Ingestor, InMemoryStore
from connectors.ingestion.embedding import from_env as embedder_from_env
from connectors.ingestion.pg_store import PostgresStore
from connectors.stub.fixture_connector import FixtureConnector
from fixtures.loader import State

log = logging.getLogger(__name__)


@dataclass
class Runtime:
    settings: Settings
    brain: Brain
    audit: AuditService
    audit_store: AuditStore
    consumer: OutboxConsumer
    advance: Callable[[str, bool], None] | None = None     # (event_id, ingest) - fixture mode only
    reset: Callable[[], None] | None = None
    tamper: Callable[[int], None] | None = None

    def sync(self) -> int:
        """Apply what ingestion's outbox says before serving: dropped caches, `acl_change_observed` events."""
        try:
            return self.consumer.drain()
        except Exception:                                  # noqa: BLE001 - never block a request on the outbox
            log.exception("outbox drain failed")
            return 0


def fixture_runtime(settings: Settings | None = None, *, sources=("confluence", "jira", "slack", "gdrive")) -> Runtime:
    settings = settings or Settings(floor_latency_ms=0, dev_auth=True, sources=tuple(sources))
    state = State()
    connectors = {s: FixtureConnector(s, state) for s in settings.sources}
    store = InMemoryStore()
    embedder = FakeEmbedder()
    ingestor = Ingestor(connectors.values(), store, embedder)
    ingestor.run_once()
    audit_store = MemoryAuditStore(signer=Signer(settings.audit_signing_key), checkpoint_every=settings.checkpoint_every)
    brain = Brain(settings, connectors, MemoryIndex(store), store, embedder, TemplateGenerator(), audit_store)
    consumer = OutboxConsumer(store, brain.resolver, brain.pdp, record=audit_store.append)
    runtime = Runtime(settings, brain, AuditService(audit_store, brain), audit_store, consumer)

    def advance(event_id: str, ingest: bool = True) -> None:
        for c in connectors.values():
            c.advance(event_id)
        if ingest:
            ingestor.run_once()
        runtime.sync()

    def reset() -> None:
        fresh = fixture_runtime(settings, sources=sources)
        runtime.brain, runtime.audit, runtime.audit_store, runtime.consumer = fresh.brain, fresh.audit, fresh.audit_store, fresh.consumer
        runtime.advance, runtime.reset, runtime.tamper = fresh.advance, fresh.reset, fresh.tamper

    def tamper(seq: int) -> None:
        runtime.audit_store.tamper(seq, lambda e: e.setdefault("query", {}).__setitem__("text", "TAMPERED"))

    runtime.advance, runtime.reset, runtime.tamper = advance, reset, tamper
    return runtime


def env_runtime(settings: Settings | None = None) -> Runtime:
    settings = settings or Settings.from_env()
    from connectors.ingestion.__main__ import SOURCES as FACTORIES
    connectors = {}
    for name in settings.sources:
        try:
            connectors[name] = FACTORIES[name]()
        except Exception as exc:                            # noqa: BLE001 - a source we cannot reach is left out
            log.warning("source %s not configured (%s): skipped", name, type(exc).__name__)
    store = PostgresStore.connect(settings.database_url)
    index = PostgresIndex.connect(settings.database_url)
    audit_store = PostgresAuditStore.connect(settings.database_url, signer=Signer(settings.audit_signing_key),
                                             checkpoint_every=settings.checkpoint_every)
    embedder = embedder_from_env(settings.embedding_backend)
    embedder.embed(["warm up"])      # bge-m3 loads on first use (about 30 s warm, minutes cold): pay it now, not on the first question
    scorer = scorer_from_env(settings.checker_model)
    if scorer is not None:
        scorer.score([("warm up", "warm up")])                  # load the grounding model now, not on the first answer
    brain = Brain(settings, connectors, index, store, embedder, generator_from_settings(settings), audit_store, scorer=scorer)
    consumer = OutboxConsumer(store, brain.resolver, brain.pdp, record=audit_store.append)
    consumer.last_seq = max([e.seq or 0 for e in store.events_after(0, 100_000)] or [0])   # history is A's, not news
    runtime = Runtime(settings, brain, AuditService(audit_store, brain), audit_store, consumer)
    if settings.dev_auth:
        runtime.tamper = lambda seq: audit_store.tamper(seq, lambda e: e.setdefault("query", {}).__setitem__("text", "TAMPERED"))
    return runtime

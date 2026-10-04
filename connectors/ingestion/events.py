"""Permission events ingestion observes, and where they go.

Two kinds, matching "Two kinds of permission change" in acl-model.md:
- principal_change: a person's memberships changed. No document did, so the index is untouched.
- acl_change:       a document's own ACL changed. Ingestion rewrites the index right after emitting the event.

The consumer (Workstream B) drops what it cached for that person or document and records an
`acl_change_observed` audit event. The audit log is hash-chained and B's audit service is its only writer,
so ingestion never inserts into `audit_events`. It appends to an outbox (`ingestion_events`) instead, and
the consumer reads rows past the last `seq` it handled. See README.md in this directory.
"""
import logging
from dataclasses import dataclass
from typing import Literal, Protocol

from connectors.base import Source

log = logging.getLogger(__name__)

EventKind = Literal["principal_change", "acl_change"]


@dataclass(frozen=True)
class IngestionEvent:
    kind: EventKind
    source: Source
    detected_at: str                  # when the connector saw the change
    observed_at: str                  # when ingestion processed it
    principal: str | None = None      # principal_change: "user:<canonical email>"
    token: str | None = None          # principal_change: the token gained or lost
    doc_id: str | None = None         # acl_change: the document
    snapshot_hash: str | None = None  # acl_change: hash of the new ACL (the open row in acl_snapshots)
    seq: int | None = None            # set once stored in the outbox; strictly increasing


class EventSink(Protocol):
    def emit(self, event: IngestionEvent) -> None:
        """Raise to have the change retried: ingestion does not move its cursor past a failed event.
        Delivery is at least once, so the same event may arrive twice after a crash."""


class EventLog(Protocol):
    """The part of a Store the outbox needs."""

    def append_event(self, event: IngestionEvent) -> int: ...


class OutboxSink:
    """Default sink: appends to the store's outbox, so events survive restarts and wait for their consumer."""

    def __init__(self, store: EventLog) -> None:
        self._store = store

    def emit(self, event: IngestionEvent) -> None:
        seq = self._store.append_event(event)
        log.info("ingestion_event seq=%s kind=%s source=%s principal=%s token=%s doc_id=%s",
                 seq, event.kind, event.source, event.principal, event.token, event.doc_id)


class RecordingSink:
    """Keeps events in memory. For tests."""

    def __init__(self) -> None:
        self.events: list[IngestionEvent] = []

    def emit(self, event: IngestionEvent) -> None:
        self.events.append(event)

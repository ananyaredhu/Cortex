"""Where `principal_change` goes.

A person's memberships changed; no document did, so the index is untouched. The consumer (Workstream B)
drops cached identities and decisions for that person and records an `acl_change_observed` audit event.
The audit log is hash-chained and B's audit service is its only writer, so ingestion hands the event to a
sink instead of inserting into `audit_events` itself.
"""
import logging
from dataclasses import dataclass
from typing import Protocol

from connectors.base import Source

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PrincipalChange:
    source: Source
    principal: str      # "user:<canonical email>"
    token: str          # the token gained or lost
    detected_at: str    # when the connector saw it
    observed_at: str    # when ingestion processed it


class EventSink(Protocol):
    def principal_changed(self, event: PrincipalChange) -> None:
        """Raise to have the change retried: ingestion does not move its cursor past a failed event."""


class RecordingSink:
    """Default sink: logs the event and keeps it in memory. Replace it once B exposes an audit writer."""

    def __init__(self) -> None:
        self.events: list[PrincipalChange] = []

    def principal_changed(self, event: PrincipalChange) -> None:
        self.events.append(event)
        log.info("acl_change_observed source=%s principal=%s token=%s detected_at=%s",
                 event.source, event.principal, event.token, event.detected_at)

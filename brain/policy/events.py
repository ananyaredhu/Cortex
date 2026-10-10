"""Consumer of ingestion's outbox (`connectors/ingestion/README.md`, "The outbox").

A `principal_change` drops the cached identity and decisions of that person; an `acl_change` drops the decisions
and derived artifacts that involve the document. Each event is also recorded in the audit log as
`acl_change_observed`, then its `seq` is remembered. At-least-once delivery is fine: dropping a cache twice is harmless.
"""
from collections.abc import Callable

from connectors.ingestion.store import Store

from .identity import IdentityResolver
from .pdp import PDP


class OutboxConsumer:
    def __init__(self, store: Store, resolver: IdentityResolver, pdp: PDP, *,
                 on_doc_change: Callable[[str], None] | None = None,
                 record: Callable[[dict], None] | None = None) -> None:
        self._store = store
        self._resolver = resolver
        self._pdp = pdp
        self._on_doc_change = on_doc_change
        self._record = record
        self.last_seq = 0

    def drain(self, limit: int = 100) -> int:
        """Handle every outbox event after the last one seen. Returns how many were handled."""
        handled = 0
        while True:
            events = self._store.events_after(self.last_seq, limit)
            if not events:
                return handled
            for event in events:
                if event.kind == "principal_change" and event.principal:
                    email = event.principal.removeprefix("user:")
                    self._resolver.invalidate(email)
                    self._pdp.invalidate_user(email)
                    self._resolver.resolve(email)       # record the person's new token set now, not at their next question
                elif event.kind == "acl_change" and event.doc_id:
                    self._pdp.invalidate_doc(event.doc_id)
                    if self._on_doc_change:
                        self._on_doc_change(event.doc_id)
                if self._record:
                    self._record({"event_type": "acl_change_observed", "request_id": f"ing_{event.seq}",
                                  "actor": {"user_id": "system:ingestion", "roles": [], "client": "ingestion"},
                                  "change": {"kind": event.kind, "source": event.source,
                                             "principal": event.principal, "token": event.token,
                                             "doc_id": event.doc_id, "detected_at": event.detected_at}})
                self.last_seq = max(self.last_seq, event.seq or 0)
                handled += 1

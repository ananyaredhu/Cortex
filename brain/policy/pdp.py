"""Policy decision point: the just-in-time check (01-architecture.md, step 4).

For each candidate document the PDP asks the owning connector's live `check_access`. The answer is cached for a
few seconds, keyed by asker and document, and dropped on permission events. Any error is a deny. The decision
carries the evidence the audit log needs for replay (acl-model.md, "Decisions are recorded with evidence").

`AccessDecision` from the connector never leaves this module: `Decision` is what the pipeline sees, and
`Decision.audit_view` is what the log stores (denied IDs salted-hashed).
"""
import hashlib
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from connectors.base import Connector

from .identity import AskerIdentity


@dataclass(frozen=True)
class Decision:
    doc_id: str
    allowed: bool
    proof_path: tuple[str, ...]
    acl_snapshot_hash: str
    policy_version: str
    doc_version: str | None
    evaluated_at: str
    reason: str = ""                 # no_access | not_found | no_identity | error (denies only)
    jit_checked: bool = True

    def audit_view(self, salt: str) -> dict:
        if self.allowed:
            return {"doc_id": self.doc_id, "allowed": True, "proof_path": list(self.proof_path),
                    "acl_snapshot_hash": self.acl_snapshot_hash, "policy_version": self.policy_version,
                    "doc_version": self.doc_version, "jit_checked": self.jit_checked}
        return {"doc_id_hash": hash_denied(self.doc_id, salt), "allowed": False, "reason": self.reason,
                "acl_snapshot_hash": self.acl_snapshot_hash, "policy_version": self.policy_version}


def hash_denied(doc_id: str, salt: str) -> str:
    return "sha256:" + hashlib.sha256(f"{salt}\x00{doc_id}".encode()).hexdigest()


def source_of(doc_id: str) -> str:
    return doc_id.split(":", 1)[0]


class PDP:
    def __init__(self, connectors: Mapping[str, Connector], *, policy_version: str, ttl_s: float = 15.0,
                 clock: Callable[[], float] = time.monotonic, now: Callable[[], str] | None = None,
                 workers: int = 8) -> None:
        self._connectors = dict(connectors)
        self.policy_version = policy_version
        self._ttl = ttl_s
        self._clock = clock
        self._now = now or _utc_now
        self._cache: dict[tuple[str, str], tuple[float, Decision]] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="pdp")

    # -- decisions ------------------------------------------------------------------------------
    def check(self, asker: AskerIdentity, doc_id: str, doc_version: str | None = None) -> Decision:
        key = (asker.email, doc_id)
        now = self._clock()
        with self._lock:
            hit = self._cache.get(key)
            if hit is not None and now - hit[0] < self._ttl and hit[1].doc_version == doc_version:
                return hit[1]
        decision = self._decide(asker, doc_id, doc_version)
        with self._lock:
            self._cache[key] = (now, decision)
        return decision

    def check_many(self, asker: AskerIdentity, docs: Iterable[tuple[str, str | None]]) -> list[Decision]:
        """`docs` are (doc_id, indexed version) pairs. Checked in parallel: a real platform takes hundreds of ms."""
        docs = list(docs)
        return list(self._pool.map(lambda d: self.check(asker, d[0], d[1]), docs))

    def _decide(self, asker: AskerIdentity, doc_id: str, doc_version: str | None) -> Decision:
        source = source_of(doc_id)
        connector = self._connectors.get(source)
        identity = asker.identity(source)
        at = self._now()
        if connector is None or identity is None:
            return Decision(doc_id, False, (), "", self.policy_version, doc_version, at, "no_identity")
        try:
            verdict = connector.check_access(identity, doc_id)
        except Exception:                                   # noqa: BLE001 - fail closed
            return Decision(doc_id, False, (), "", self.policy_version, doc_version, at, "error")
        if not verdict.allowed:
            reason = "not_found" if not verdict.acl_snapshot_hash else "no_access"
            return Decision(doc_id, False, (), verdict.acl_snapshot_hash or "", self.policy_version, doc_version, at, reason)
        return Decision(doc_id, True, tuple(verdict.proof_path), verdict.acl_snapshot_hash, self.policy_version,
                        doc_version, verdict.evaluated_at or at)

    # -- cache invalidation (brain.policy.events) -------------------------------------------------
    def invalidate_user(self, email: str) -> None:
        with self._lock:
            for key in [k for k in self._cache if k[0] == email]:
                del self._cache[key]

    def invalidate_doc(self, doc_id: str) -> None:
        with self._lock:
            for key in [k for k in self._cache if k[1] == doc_id]:
                del self._cache[key]

    def invalidate_all(self) -> None:
        with self._lock:
            self._cache.clear()


def _utc_now() -> str:
    from datetime import UTC, datetime
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")

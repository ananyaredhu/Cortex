"""The asker's access token set (acl-model.md, "Tokens").

`user:<canonical email>` plus the union of `PlatformIdentity.groups` from every connector. A connector that has no
mapping for the person, or that fails, contributes nothing: fail closed on that platform. The result is cached for a
short time and dropped on a `principal_change` event (brain.policy.events).
"""
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from connectors.base import Connector, PlatformIdentity


@dataclass
class AskerIdentity:
    email: str
    tokens: frozenset[str]
    identities: dict[str, PlatformIdentity] = field(default_factory=dict)   # per source, only where mapped
    unavailable: tuple[str, ...] = ()                                        # sources whose connector failed
    resolved_at: float = 0.0

    def identity(self, source: str) -> PlatformIdentity | None:
        return self.identities.get(source)

    def sources(self) -> tuple[str, ...]:
        """The platforms the person has an identity on: the only ones worth searching."""
        return tuple(sorted(self.identities))


class IdentityResolver:
    def __init__(self, connectors: Mapping[str, Connector], *, ttl_s: float = 60.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._connectors = dict(connectors)
        self._ttl = ttl_s
        self._clock = clock
        self._cache: dict[str, AskerIdentity] = {}
        self._lock = threading.Lock()
        self.on_resolved: Callable[[AskerIdentity], None] | None = None   # called after each live resolution (not a cache hit)

    def resolve(self, email: str) -> AskerIdentity:
        now = self._clock()
        with self._lock:
            hit = self._cache.get(email)
            if hit is not None and now - hit.resolved_at < self._ttl:
                return hit
        identities: dict[str, PlatformIdentity] = {}
        unavailable: list[str] = []
        tokens = {f"user:{email}"}
        for source, connector in self._connectors.items():
            try:
                identity = connector.resolve_identity(email)
            except Exception:                       # noqa: BLE001 - any failure means no access on that platform
                unavailable.append(source)
                continue
            if identity is None:
                continue
            identities[source] = identity
            tokens.update(identity.groups)
        asker = AskerIdentity(email, frozenset(tokens), identities, tuple(unavailable), now)
        with self._lock:
            self._cache[email] = asker
        if self.on_resolved is not None:
            self.on_resolved(asker)
        return asker

    def invalidate(self, email: str | None = None) -> None:
        with self._lock:
            if email is None:
                self._cache.clear()
            else:
                self._cache.pop(email, None)

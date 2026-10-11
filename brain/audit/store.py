"""The one writer of the audit log. In memory for tests, Postgres (`audit_events` in db/init.sql) otherwise.

`append` links the event into the chain (seq, prev_hash, hash) under a lock, so there is exactly one head.
`tamper` exists for the demo of `/verify` failing and is only wired up in development mode.
"""
import json
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

import psycopg
from psycopg.types.json import Jsonb

from . import chain
from .anchor import HeadAnchor, sign_head


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class AuditStore(Protocol):
    def append(self, event: dict) -> dict: ...
    def events(self, *, user: str | None = None, request_id: str | None = None, limit: int = 1000) -> list[dict]: ...
    def all(self) -> list[dict]: ...
    def count(self) -> int: ...
    def tamper(self, seq: int, mutate: Callable[[dict], None]) -> None: ...
    def truncate(self, keep: int) -> None: ...
    def adopt_head(self) -> None: ...


class MemoryAuditStore:
    def __init__(self, *, signer: chain.Signer | None = None, checkpoint_every: int = 100,
                 clock: Callable[[], str] = utc_now, anchor: HeadAnchor | None = None) -> None:
        self.anchor = anchor
        self._events: list[dict] = []
        self._lock = threading.Lock()
        self._signer = signer or chain.Signer()
        self._every = checkpoint_every
        self._clock = clock

    @property
    def public_key_hex(self) -> str:
        return self._signer.public_key_hex

    @property
    def pinned_public_key_hex(self) -> str | None:
        """The key every checkpoint must verify against: only a configured (persistent) key can be pinned.
        With a key made for this run, an earlier run's checkpoints legitimately carry another key (fix F3)."""
        return self._signer.public_key_hex if self._signer.persistent else None

    def append(self, event: dict) -> dict:
        with self._lock:
            event = dict(event)
            event.setdefault("ts", self._clock())
            linked = self._link(event)
            self._events.append(linked)
            if self._every and len(self._events) % self._every == 0:
                self._events.append(self._link(chain.checkpoint_event(linked["seq"], linked["hash"], self._signer, self._clock())))
            head = self._events[-1]
            if self.anchor is not None:
                self.anchor.publish(sign_head(head["seq"], head["hash"], self._signer, self._clock()))
            return linked

    def _link(self, event: dict) -> dict:
        prev = self._events[-1]["hash"] if self._events else chain.GENESIS
        return chain.link(event, len(self._events) + 1, prev)

    def all(self) -> list[dict]:
        with self._lock:
            return [dict(e) for e in self._events]

    def count(self) -> int:
        return len(self._events)

    def events(self, *, user: str | None = None, request_id: str | None = None, limit: int = 1000) -> list[dict]:
        out = [e for e in self.all()
               if (user is None or e.get("actor", {}).get("user_id") == user)
               and (request_id is None or e.get("request_id") == request_id)]
        return out[-limit:]

    def tamper(self, seq: int, mutate: Callable[[dict], None]) -> None:
        with self._lock:
            mutate(self._events[seq - 1])

    def truncate(self, keep: int) -> None:
        """Demo only: delete every event after the first `keep`, as an insider with database access could."""
        with self._lock:
            del self._events[keep:]

    def adopt_head(self) -> None:
        """Record the current head in an anchor that has none (first run with an anchor over an existing log)."""
        with self._lock:
            if self.anchor is not None and self.anchor.latest() is None and self._events:
                head = self._events[-1]
                self.anchor.publish(sign_head(head["seq"], head["hash"], self._signer, self._clock()))


class PostgresAuditStore:
    """`audit_events`: seq, ts, request_id, event_type, actor, event (the whole event minus hash), prev_hash, hash.
    The connection is autocommit; `append` serializes writers with a table lock inside its own transaction."""

    def __init__(self, conn: psycopg.Connection, *, signer: chain.Signer | None = None, checkpoint_every: int = 100,
                 clock: Callable[[], str] = utc_now, anchor: HeadAnchor | None = None) -> None:
        self.anchor = anchor
        self._conn = conn
        self._lock = threading.Lock()
        self._signer = signer or chain.Signer()
        self._every = checkpoint_every
        self._clock = clock

    @classmethod
    def connect(cls, url: str, **kwargs) -> "PostgresAuditStore":
        return cls(psycopg.connect(url, autocommit=True, connect_timeout=5), **kwargs)

    @property
    def public_key_hex(self) -> str:
        return self._signer.public_key_hex

    @property
    def pinned_public_key_hex(self) -> str | None:
        """The key every checkpoint must verify against: only a configured (persistent) key can be pinned.
        With a key made for this run, an earlier run's checkpoints legitimately carry another key (fix F3)."""
        return self._signer.public_key_hex if self._signer.persistent else None

    def append(self, event: dict) -> dict:
        with self._lock, self._conn.transaction():
            self._conn.execute("LOCK TABLE audit_events IN EXCLUSIVE MODE")
            event = dict(event)
            event.setdefault("ts", self._clock())
            linked = self._insert(event)
            head = linked
            if self._every and linked["seq"] % self._every == 0:
                head = self._insert(chain.checkpoint_event(linked["seq"], linked["hash"], self._signer, self._clock()))
        if self.anchor is not None:                      # after the commit: an anchor never gets ahead of what was stored
            self.anchor.publish(sign_head(head["seq"], head["hash"], self._signer, self._clock()))
        return linked

    def _insert(self, event: dict) -> dict:
        row = self._conn.execute("SELECT seq, hash FROM audit_events ORDER BY seq DESC LIMIT 1").fetchone()
        seq, prev = (row[0] + 1, row[1]) if row else (1, chain.GENESIS)
        linked = chain.link(event, seq, prev)
        body = {k: v for k, v in linked.items() if k != "hash"}
        self._conn.execute(
            "INSERT INTO audit_events (seq, ts, request_id, event_type, actor, event, prev_hash, hash)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (seq, linked["ts"], linked["request_id"], linked["event_type"], Jsonb(linked["actor"]), Jsonb(body),
             linked["prev_hash"], linked["hash"]))
        return linked

    @staticmethod
    def _row(r) -> dict:
        event = r[0] if isinstance(r[0], dict) else json.loads(r[0])
        event["hash"] = r[1]
        return event

    def all(self) -> list[dict]:
        rows = self._conn.execute("SELECT event, hash FROM audit_events ORDER BY seq").fetchall()
        return [self._row(r) for r in rows]

    def count(self) -> int:
        return self._conn.execute("SELECT count(*) FROM audit_events").fetchone()[0]

    def events(self, *, user: str | None = None, request_id: str | None = None, limit: int = 1000) -> list[dict]:
        rows = self._conn.execute(
            "SELECT event, hash FROM (SELECT event, hash, seq FROM audit_events"
            " WHERE (%(user)s::text IS NULL OR actor->>'user_id' = %(user)s)"
            " AND (%(rid)s::text IS NULL OR request_id = %(rid)s) ORDER BY seq DESC LIMIT %(limit)s) t ORDER BY seq",
            {"user": user, "rid": request_id, "limit": limit}).fetchall()
        return [self._row(r) for r in rows]

    def tamper(self, seq: int, mutate: Callable[[dict], None]) -> None:
        """Demo only: edit a stored row in place, which `/verify` must then detect."""
        row = self._conn.execute("SELECT event FROM audit_events WHERE seq = %s", (seq,)).fetchone()
        event = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        mutate(event)
        self._conn.execute("UPDATE audit_events SET event = %s WHERE seq = %s", (Jsonb(event), seq))

    def truncate(self, keep: int) -> None:
        """Demo only: delete every row after the first `keep`, as an insider with database access could."""
        self._conn.execute("DELETE FROM audit_events WHERE seq > %s", (keep,))

    def adopt_head(self) -> None:
        """Record the current head in an anchor that has none (first run with an anchor over an existing log)."""
        row = self._conn.execute("SELECT seq, hash FROM audit_events ORDER BY seq DESC LIMIT 1").fetchone()
        if self.anchor is not None and self.anchor.latest() is None and row:
            self.anchor.publish(sign_head(row[0], row[1], self._signer, self._clock()))

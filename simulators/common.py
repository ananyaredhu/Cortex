"""Pieces the simulators share: errors, clock, the change feed and webhook delivery."""
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from connectors.base import Change
from connectors.base import DocumentNotFound as DocumentNotFound  # re-exported for the simulators' connectors


class NotFound(LookupError):
    """Unknown project, issue, page, user, role..."""


class Invalid(ValueError):
    """A request the simulator cannot apply (bad id, duplicate, bad cursor...)."""


class Gone(LookupError):
    """A change-feed cursor from another run of the log: the simulator restarted or was reset. HTTP 410."""


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def norm_email(email: str) -> str:
    return email.strip().lower()


@dataclass
class ChangeEntry:
    seq: int
    type: str                    # upsert | delete | acl_change | principal_change
    item_id: str | None          # native id (issue key, page id); None for principal_change
    doc_id: str | None           # "<source>:<native id>"; None for principal_change
    detected_at: str
    event: str                   # webhook event name
    principal: str | None = None  # principal_change only: "user:<email>"
    token: str | None = None      # principal_change only: the token gained or lost

    def as_json(self) -> dict:
        return {"type": self.type, "item_id": self.item_id, "doc_id": self.doc_id, "principal": self.principal,
                "token": self.token, "detected_at": self.detected_at}


def to_change(entry: dict) -> Change:
    """A `/sim/changes` entry as the contract's Change."""
    return Change(entry["type"], entry["doc_id"], entry["detected_at"], entry.get("principal"), entry.get("token"))


class ChangeLog:
    """Append-only log behind `list_changes`, plus the full crawl a null cursor starts."""

    def __init__(self, clock: Callable[[], str] = utc_now) -> None:
        self._clock = clock
        self.entries: list[ChangeEntry] = []
        self.run = uuid.uuid4().hex[:8]     # cursors name the run of the log they belong to

    def emit(self, type_: str, item_id: str, doc_id: str, event: str) -> None:
        """A document was created, edited or deleted, or its own ACL changed."""
        self.entries.append(ChangeEntry(len(self.entries), type_, item_id, doc_id, self._clock(), event))

    def emit_principal(self, principal: str, token: str, event: str) -> None:
        """A person gained or lost a token. No document changed, so this is one entry however many documents it reaches."""
        self.entries.append(ChangeEntry(len(self.entries), "principal_change", None, None, self._clock(), event, principal, token))

    def clear(self) -> None:
        self.entries.clear()
        self.run = uuid.uuid4().hex[:8]     # a reset is a new log: older cursors no longer mean anything

    def read(self, cursor: str | None, limit: int, live: dict[str, str]) -> tuple[list[ChangeEntry], str, bool]:
        """Returns (entries, next_cursor, has_more). `live` maps every existing item id to its doc id.

        cursor=None starts a full crawl: every live item as an upsert, paged by item id. The crawl remembers
        where the log stood when it began, so nothing that changes during the crawl is lost.
        Cursors are "<run>.<position>" or "crawl:<run>.<position>:<after>". One from another run of the log (the
        simulator restarted or was reset) raises Gone: the consumer must crawl again.
        """
        if limit < 1:
            raise Invalid("limit must be at least 1")
        try:
            if cursor is None:
                return self._crawl(len(self.entries), "", limit, live)
            if cursor.startswith("crawl:"):
                _, log_pos, after = cursor.split(":", 2)
                return self._crawl(self._position(log_pos), after, limit, live)
            start = self._position(cursor)
        except ValueError as exc:
            raise Invalid(f"Bad cursor: {cursor!r}") from exc
        batch = self.entries[start:start + limit]
        end = start + len(batch)
        return batch, self._cursor(end), end < len(self.entries)

    def _cursor(self, pos: int) -> str:
        return f"{self.run}.{pos}"

    def _position(self, mark: str) -> int:
        """`mark` is "<run>.<position>". Another run's, or a bare number (the format before runs), is Gone;
        anything else malformed is a ValueError (400)."""
        run, dot, raw = mark.partition(".")
        if (dot and run != self.run) or (not dot and mark.isdigit()):
            raise Gone("This cursor belongs to an earlier run of the change log; start a full crawl")
        pos = int(raw)
        if not 0 <= pos <= len(self.entries):
            raise ValueError(pos)
        return pos

    def _crawl(self, log_pos: int, after: str, limit: int, live: dict[str, str]) -> tuple[list[ChangeEntry], str, bool]:
        ids = sorted(i for i in live if i > after)
        batch = ids[:limit]
        now = self._clock()
        entries = [ChangeEntry(-1, "upsert", i, live[i], now, "crawl") for i in batch]
        if len(ids) > limit:
            return entries, f"crawl:{self._cursor(log_pos)}:{batch[-1]}", True
        return entries, self._cursor(log_pos), log_pos < len(self.entries)


def webhook_payloads(entries: list[ChangeEntry], item_field: str, id_field: str) -> list[dict]:
    """Webhook bodies for new log entries. `item_field`/`id_field` name the platform's object, e.g. ("issue", "key")."""
    out = []
    for e in entries:
        payload: dict = {"webhookEvent": e.event, "timestamp": e.detected_at, "_simulator": {"seq": e.seq, **e.as_json()}}
        if e.item_id is not None:
            payload[item_field] = {id_field: e.item_id}
        out.append(payload)
    return out


def deliver(urls: list[str], payloads: list[dict]) -> None:
    """Best-effort webhook delivery. A lost webhook is fine: the change feed is the fallback."""
    for url in urls:
        for payload in payloads:
            try:
                httpx.post(url, json=payload, timeout=2.0)
            except httpx.HTTPError:
                pass

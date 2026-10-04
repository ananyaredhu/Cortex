"""Pieces the simulators share: errors, clock, the change feed and webhook delivery."""
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx


class NotFound(LookupError):
    """Unknown project, issue, user, role..."""


class Invalid(ValueError):
    """A request the simulator cannot apply (bad id, duplicate, bad cursor...)."""


class DocumentNotFound(LookupError):
    """Raised by a connector's `fetch` and `version`. Moves to connectors/base.py once contract 0.2 is merged."""


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def norm_email(email: str) -> str:
    return email.strip().lower()


@dataclass
class ChangeEntry:
    seq: int
    type: str            # upsert | delete | acl_change
    item_id: str         # native id (issue key, page id)
    doc_id: str          # "<source>:<native id>"
    detected_at: str
    event: str           # webhook event name


class ChangeLog:
    """Append-only log behind `list_changes`, plus the full crawl a null cursor starts."""

    def __init__(self, clock: Callable[[], str] = utc_now) -> None:
        self._clock = clock
        self.entries: list[ChangeEntry] = []

    def emit(self, type_: str, item_id: str, doc_id: str, event: str) -> None:
        self.entries.append(ChangeEntry(len(self.entries), type_, item_id, doc_id, self._clock(), event))

    def clear(self) -> None:
        self.entries.clear()

    def read(self, cursor: str | None, limit: int, live: dict[str, str]) -> tuple[list[ChangeEntry], str, bool]:
        """Returns (entries, next_cursor, has_more). `live` maps every existing item id to its doc id.

        cursor=None starts a full crawl: every live item as an upsert, paged by item id. The crawl remembers
        where the log stood when it began, so nothing that changes during the crawl is lost.
        """
        if limit < 1:
            raise Invalid("limit must be at least 1")
        try:
            if cursor is None:
                return self._crawl(len(self.entries), "", limit, live)
            if cursor.startswith("crawl:"):
                _, log_pos, after = cursor.split(":", 2)
                return self._crawl(self._position(int(log_pos)), after, limit, live)
            start = self._position(int(cursor))
        except ValueError as exc:
            raise Invalid(f"Bad cursor: {cursor!r}") from exc
        batch = self.entries[start:start + limit]
        end = start + len(batch)
        return batch, str(end), end < len(self.entries)

    def _position(self, pos: int) -> int:
        if not 0 <= pos <= len(self.entries):
            raise ValueError(pos)
        return pos

    def _crawl(self, log_pos: int, after: str, limit: int, live: dict[str, str]) -> tuple[list[ChangeEntry], str, bool]:
        ids = sorted(i for i in live if i > after)
        batch = ids[:limit]
        now = self._clock()
        entries = [ChangeEntry(-1, "upsert", i, live[i], now, "crawl") for i in batch]
        if len(ids) > limit:
            return entries, f"crawl:{log_pos}:{batch[-1]}", True
        return entries, str(log_pos), log_pos < len(self.entries)


def deliver(urls: list[str], payloads: list[dict]) -> None:
    """Best-effort webhook delivery. A lost webhook is fine: the change feed is the fallback."""
    for url in urls:
        for payload in payloads:
            try:
                httpx.post(url, json=payload, timeout=2.0)
            except httpx.HTTPError:
                pass

"""Slack events through Socket Mode. Their only effect is to tell the connector what changed and wake ingestion.

Socket Mode is an outbound websocket opened with the app-level token (SLACK_APP_TOKEN, scope `connections:write`):
no public URL and no tunnel. The listener runs in a thread inside the ingestion process, so there is still a
single writer to the index and the outbox (`python -m connectors.ingestion --poll ... --slack-events`).

Every envelope is acknowledged at once. From an event the listener keeps IDs only (channel, thread ts) and never
logs or stores message text, the websocket URL or the token. Events are hints: a lost one is caught by the next
scheduled scan, and a duplicate costs one more fetch.

Events the app must subscribe to, all covered by the bot's existing read scopes: `message.channels`,
`message.groups`, `member_joined_channel`, `member_left_channel`, `channel_created`, `channel_deleted`,
`channel_rename`, `channel_archive`, `channel_unarchive`, `group_rename`, `group_archive`, `group_unarchive`,
`group_left`, `user_change`, `team_join`.
"""
import json
import logging
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass

import httpx
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

OPEN_URL = "https://slack.com/api/apps.connections.open"
MAX_BACKOFF = 60.0
_THREAD_SUBTYPES = {None, "file_share", "me_message", "thread_broadcast", "message_changed", "message_deleted", "message_replied"}
_STRUCTURE_SUBTYPES = {"channel_join", "channel_leave", "group_join", "group_leave", "channel_convert_to_private",
                       "channel_convert_to_public"}
# Memberships, channels and accounts: what changes access. All of these send the connector back to a full scan.
STRUCTURE_EVENTS = {"member_joined_channel", "member_left_channel", "channel_created", "channel_deleted", "channel_rename",
                    "channel_archive", "channel_unarchive", "channel_left", "channel_shared", "channel_unshared",
                    "group_open", "group_close", "group_deleted", "group_rename", "group_archive", "group_unarchive",
                    "group_left", "user_change", "team_join"}

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Hint:
    """What one event means for ingestion: a thread changed, or memberships/channels changed."""
    channel: str | None = None
    thread_ts: str | None = None
    structure: bool = False


def classify(event: dict) -> Hint | None:
    """The hint in one Events API `event`, or None when it does not matter (DMs, bot chatter we do not index)."""
    kind = event.get("type")
    if kind in STRUCTURE_EVENTS:
        return Hint(structure=True)
    if kind != "message" or event.get("channel_type") in ("im", "mpim"):
        return None
    subtype = event.get("subtype")
    if subtype in _STRUCTURE_SUBTYPES:
        return Hint(structure=True)
    if subtype not in _THREAD_SUBTYPES:
        return None
    if subtype in ("message_changed", "message_replied"):
        message = event.get("message") or {}
    elif subtype == "message_deleted":
        message = event.get("previous_message") or {"ts": event.get("deleted_ts")}
    else:
        message = event
    ts = message.get("thread_ts") or message.get("ts")
    channel = event.get("channel")
    if not isinstance(channel, str) or not isinstance(ts, str):
        return None
    return Hint(channel=channel, thread_ts=ts)


def open_connection(app_token: str) -> str:
    """A fresh websocket URL from Slack. Each URL is single use and must never be logged."""
    response = httpx.post(OPEN_URL, headers={"Authorization": f"Bearer {app_token}"}, timeout=10.0)
    response.raise_for_status()
    body = response.json()
    if not body.get("ok") or not body.get("url"):
        raise RuntimeError(f"apps.connections.open: {body.get('error') or 'no url'}")
    return str(body["url"])


class SocketModeListener:
    def __init__(self, on_hint: Callable[[Hint], None], open_url: Callable[[], str]) -> None:
        """`on_hint` is called on the listener's thread for every event that matters. `open_url` gets a websocket URL."""
        self._on_hint = on_hint
        self._open_url = open_url
        self._stop = threading.Event()
        self._ws = None
        self._thread: threading.Thread | None = None
        self.connected = threading.Event()
        self.connections = 0          # hellos received
        self.events = 0               # events_api envelopes received
        self.hints = 0                # of which mattered

    @classmethod
    def from_env(cls, on_hint: Callable[[Hint], None]) -> "SocketModeListener":
        token = (os.environ.get("SLACK_APP_TOKEN") or "").strip()
        if not token.startswith("xapp-"):
            raise RuntimeError("SLACK_APP_TOKEN is not set to an app-level token (put it in .env; never paste it anywhere else)")
        return cls(on_hint, lambda: open_connection(token))

    def start(self) -> "SocketModeListener":
        self._thread = threading.Thread(target=self._run, name="slack-events", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        ws = self._ws
        if ws is not None:
            ws.close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                with connect(self._open_url(), open_timeout=10, close_timeout=2) as ws:
                    self._ws = ws
                    greeted = self._session(ws)
            except Exception as exc:   # network, Slack refusing, a bad frame: say what kind, never the URL or payload
                log.warning("Slack events: connection failed (%s); retrying in %.0f s", type(exc).__name__, backoff)
                greeted = False
            finally:
                self._ws = None
                self.connected.clear()
            if greeted:
                backoff = 1.0
            else:
                self._stop.wait(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF)

    def _session(self, ws) -> bool:
        """Read envelopes until Slack asks us to reconnect or the socket closes. True if the session said hello."""
        greeted = False
        while not self._stop.is_set():
            try:
                envelope = json.loads(ws.recv())
            except ConnectionClosed:
                return greeted
            kind = envelope.get("type")
            if envelope.get("envelope_id"):
                ws.send(json.dumps({"envelope_id": envelope["envelope_id"]}))   # ack first: Slack retries after 3 s
            if kind == "hello":
                greeted = True
                self.connections += 1
                self.connected.set()
                log.info("Slack events: connected")
                if self.connections > 1:
                    self._hint(Hint(structure=True))   # events may have been missed while reconnecting: scan
            elif kind == "disconnect":
                log.info("Slack events: Slack asked to reconnect (%s)", envelope.get("reason", "no reason"))
                return greeted
            elif kind == "events_api":
                self.events += 1
                hint = classify((envelope.get("payload") or {}).get("event") or {})
                if hint is not None:
                    self._hint(hint)
        return greeted

    def _hint(self, hint: Hint) -> None:
        self.hints += 1
        try:
            self._on_hint(hint)
        except Exception as exc:
            log.warning("Slack events: handling an event failed (%s)", type(exc).__name__)

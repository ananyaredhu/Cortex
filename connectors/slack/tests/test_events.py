"""Slack events: what an event means, the Socket Mode listener over a real local websocket, and what the
connector does with the hints (connectors/slack/events.py, `note_thread`, `note_structure`, `quick_next`)."""
import json
import threading

import httpx
import pytest
from websockets.sync.server import serve

from connectors.ingestion import FakeEmbedder, Ingestor, InMemoryStore
from connectors.ingestion.__main__ import Wakeups
from connectors.slack.events import Hint, SocketModeListener, classify
from connectors.slack.tests.test_slack_connector import World


@pytest.fixture
def w() -> World:
    return World()


# -- what an event means --------------------------------------------------------------------------
@pytest.mark.parametrize("event, hint", [
    ({"type": "message", "channel": "C1", "ts": "1.000100"}, Hint("C1", "1.000100")),
    ({"type": "message", "channel": "C1", "ts": "2.000100", "thread_ts": "1.000100"}, Hint("C1", "1.000100")),
    ({"type": "message", "subtype": "message_changed", "channel": "G1",
      "message": {"ts": "2.000100", "thread_ts": "1.000100", "text": "x"}}, Hint("G1", "1.000100")),
    ({"type": "message", "subtype": "message_changed", "channel": "C1", "message": {"ts": "1.000100"}}, Hint("C1", "1.000100")),
    ({"type": "message", "subtype": "message_deleted", "channel": "C1", "deleted_ts": "2.000100",
      "previous_message": {"ts": "2.000100", "thread_ts": "1.000100"}}, Hint("C1", "1.000100")),
    ({"type": "message", "subtype": "message_deleted", "channel": "C1", "deleted_ts": "1.000100"}, Hint("C1", "1.000100")),
    ({"type": "message", "subtype": "channel_join", "channel": "C1", "ts": "3.000100"}, Hint(structure=True)),
    ({"type": "member_left_channel", "channel": "C1", "user": "U1"}, Hint(structure=True)),
    ({"type": "channel_rename", "channel": {"id": "C1"}}, Hint(structure=True)),
    ({"type": "user_change", "user": {"id": "U1"}}, Hint(structure=True)),
    ({"type": "message", "channel": "D1", "channel_type": "im", "ts": "1.000100"}, None),
    ({"type": "message", "subtype": "channel_topic", "channel": "C1", "ts": "1.000100"}, None),
    ({"type": "message", "subtype": "message_changed", "channel": "C1"}, None),
    ({"type": "reaction_added"}, None),
])
def test_classify(event, hint):
    assert classify(event) == hint


# -- the listener over a websocket ----------------------------------------------------------------
class FakeSocketMode:
    """A local websocket server speaking Socket Mode: hello, envelopes, then a disconnect."""

    def __init__(self, sessions: list[list[dict]]):
        self.sessions = sessions
        self.acks: list[str] = []
        self.opened = 0
        self.done = threading.Event()
        self._server = serve(self._handle, "127.0.0.1", 0)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def url(self) -> str:
        self.opened += 1
        return f"ws://127.0.0.1:{self._server.socket.getsockname()[1]}/link"

    def _handle(self, ws) -> None:
        envelopes = self.sessions.pop(0) if self.sessions else []
        ws.send(json.dumps({"type": "hello"}))
        for envelope in envelopes:
            ws.send(json.dumps(envelope))
            if envelope.get("envelope_id"):
                self.acks.append(json.loads(ws.recv())["envelope_id"])
        if not self.sessions:
            self.done.set()
        ws.send(json.dumps({"type": "disconnect", "reason": "refresh_requested"}))

    def close(self) -> None:
        self._server.shutdown()


def _event(envelope_id: str, event: dict) -> dict:
    return {"type": "events_api", "envelope_id": envelope_id, "payload": {"event": event}}


def test_listener_acks_every_envelope_reconnects_and_scans_after_a_reconnect():
    server = FakeSocketMode([
        [_event("e1", {"type": "message", "channel": "C1", "ts": "1.000100"}),
         _event("e2", {"type": "reaction_added"}),
         {"type": "slash_commands", "envelope_id": "e3", "payload": {}}],
        [_event("e4", {"type": "message", "subtype": "message_changed", "channel": "C1",
                       "message": {"ts": "2.000100", "thread_ts": "1.000100"}})],
    ])
    hints: list[Hint] = []
    listener = SocketModeListener(hints.append, server.url).start()
    try:
        assert server.done.wait(10)
        listener.connected.wait(5)
    finally:
        listener.stop()
        server.close()
    assert server.acks == ["e1", "e2", "e3", "e4"]
    assert server.opened >= 2 and listener.events == 3
    # After the reconnect, a structure hint: events may have been missed in between.
    assert hints[:3] == [Hint("C1", "1.000100"), Hint(structure=True), Hint("C1", "1.000100")]


def test_listener_backs_off_when_it_cannot_connect():
    opened = threading.Event()

    def refuse() -> str:
        opened.set()
        raise RuntimeError("apps.connections.open: invalid_auth")

    listener = SocketModeListener(lambda hint: None, refuse).start()
    assert opened.wait(5)
    listener.stop()
    assert not listener.connected.is_set() and listener.connections == 0


# -- the connector, told by events ----------------------------------------------------------------
def test_a_reply_edit_is_invisible_to_a_scan_and_seen_through_an_event(w):
    doc_id, channel = w.doc("slack:C_AUTH/thread-1"), w.channel("C_AUTH")
    root_ts = doc_id.split("/")[1]
    reply_ts = w.slack.post(channel, w.user("dana"), "Lifetimes of 15 minutes.", thread_ts=root_ts)
    _, cursor = w.changes(None)
    w.slack.edit(channel, reply_ts, "Lifetimes of 5 minutes.")
    changes, cursor = w.changes(cursor)
    assert changes == []                                     # the gap events close
    w.conn.note_thread(channel, root_ts)
    assert w.changes(cursor)[0] == [("upsert", doc_id, None, None)]
    assert w.conn.fetch(doc_id).body.endswith("Lifetimes of 5 minutes.")


def test_quick_pass_checks_only_the_noted_threads(w):
    doc_id, channel = w.doc("slack:C_AUTH/thread-1"), w.channel("C_AUTH")
    _, cursor = w.changes(None)
    scans = w.slack.calls["conversations.history"]
    w.slack.post(channel, w.user("dana"), "A reply.", thread_ts=doc_id.split("/")[1])
    new_ts = w.slack.post(channel, w.user("dana"), "A new thread.")
    w.conn.note_thread(channel, doc_id.split("/")[1])
    w.conn.note_thread(channel, new_ts)
    w.conn.note_thread("C0NOTREAD", "1.000100")              # a channel the bot does not read: ignored
    w.conn.quick_next()
    changes, cursor = w.changes(cursor)
    assert changes == [("upsert", doc_id, None, None), ("upsert", f"slack:{channel}/{new_ts}", None, None)]
    assert w.slack.calls["conversations.history"] == scans   # no scan
    # The new thread is now part of what the next scan compares with, so its deletion is reported. (The scan
    # also re-sends the thread with the reply, which ingestion finds unchanged.)
    w.slack.delete(channel, new_ts)
    assert w.changes(cursor)[0] == [("upsert", doc_id, None, None), ("delete", f"slack:{channel}/{new_ts}", None, None)]


def test_a_structure_event_makes_the_quick_pass_scan(w):
    channel = w.channel("C_AUTHPRIV")
    _, cursor = w.changes(None)
    w.slack.leave(channel, w.user("priya"))
    w.conn.note_structure()
    w.conn.quick_next()
    changes, _ = w.changes(cursor)
    assert changes == [("principal_change", None, "user:priya@companya.com", f"channel:{channel}")]


def test_quick_pass_scans_when_there_is_nothing_to_build_on(w):
    _, cursor = w.changes(None)
    fresh = World.__new__(World)
    fresh.slack, fresh.manifest = w.slack, w.manifest
    fresh.conn = type(w.conn)(w.slack.client(), w.identities)   # a new process: only the cursor survives
    fresh.conn.quick_next()
    scans = w.slack.calls["conversations.history"]
    fresh.conn.list_changes(cursor)
    assert w.slack.calls["conversations.history"] > scans


def test_notes_survive_a_failed_pass(w):
    doc_id, channel = w.doc("slack:C_AUTH/thread-1"), w.channel("C_AUTH")
    _, cursor = w.changes(None)
    w.conn.note_thread(channel, doc_id.split("/")[1])
    w.slack.down = True
    with pytest.raises(httpx.ConnectError):
        w.conn.list_changes(cursor)
    w.slack.down = False
    assert w.changes(cursor)[0] == [("upsert", doc_id, None, None)]


def test_ingestion_picks_up_a_reply_edit_from_an_event(w):
    store = InMemoryStore()
    ingestor = Ingestor([w.conn], store, FakeEmbedder())
    doc_id, channel = w.doc("slack:C_AUTH/thread-1"), w.channel("C_AUTH")
    reply_ts = w.slack.post(channel, w.user("dana"), "Rotate every 90 days.", thread_ts=doc_id.split("/")[1])
    ingestor.run_once()
    w.slack.edit(channel, reply_ts, "Rotate every 30 days.")
    w.conn.note_thread(channel, doc_id.split("/")[1])
    w.conn.quick_next()
    assert dict(ingestor.run_source(w.conn)) == {"indexed": 1}
    assert "Rotate every 30 days." in store.chunks_of(doc_id)[0].text


def test_wakeups_collect_sources_until_taken():
    wake = Wakeups()
    wake.ring("slack")
    wake.ring("gdrive")
    wake.ring("slack")
    assert wake.event.is_set() and wake.take() == {"slack", "gdrive"}
    assert not wake.event.is_set() and wake.take() == set()

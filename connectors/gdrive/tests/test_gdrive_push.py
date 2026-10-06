"""Drive push notifications (watch.py, webhook.py) and change detection across ingestion restarts."""
import httpx
import pytest

from connectors.gdrive import watch, webhook
from connectors.gdrive.connector import DriveConnector
from connectors.gdrive.testing import SeededDrive

POSTMORTEM = "gdrive:postmortem-pay-outage"
VENDOR = "gdrive:vendor-integration-notes"
ADDRESS = "https://brain.example.com/drive/notify"
TOKEN = "t" * 32


@pytest.fixture
def d() -> SeededDrive:
    return SeededDrive()


def _restarted(d: SeededDrive) -> DriveConnector:
    return DriveConnector(d._sessions, d.identities, d.config)


# -- change detection across restarts ------------------------------------------------------------
def test_a_restarted_connector_reports_files_deleted_or_unshared_while_it_was_down(d):
    cursor = d.list_changes(None).next_cursor
    d.drive.files["vendor-integration-notes"]["trashed"] = True
    batch = _restarted(d).list_changes(cursor)
    assert sorted((c.type, c.doc_id) for c in batch.changes) == [("delete", VENDOR), ("upsert", POSTMORTEM)]
    again = _restarted(d)                        # a third process: the vendor notes stay gone, nothing new is deleted
    first = again.list_changes(batch.next_cursor)
    assert [(c.type, c.doc_id) for c in first.changes] == [("upsert", POSTMORTEM)]
    assert again.list_changes(first.next_cursor).changes == []


def test_a_cursor_without_readable_saved_ids_falls_back_to_a_crawl(d):
    cursor = d.list_changes(None).next_cursor
    for stale in (cursor.partition("~")[0], cursor.partition("~")[0] + "~%%%", "garbage"):
        batch = _restarted(d).list_changes(stale)
        assert sorted((c.type, c.doc_id) for c in batch.changes) == [("upsert", POSTMORTEM), ("upsert", VENDOR)]


# -- opening and stopping channels ---------------------------------------------------------------
def test_open_channels_watches_every_signed_in_account_and_stop_closes_them(d, tmp_path):
    channels = watch.open_channels(d._sessions, ADDRESS, TOKEN, now=lambda: 1_000_000.0)
    assert sorted(c.account for c in channels) == sorted(d._sessions)
    assert set(d.drive.channels) == {c.id for c in channels}
    assert all(c["address"] == ADDRESS and c["token"] == TOKEN for c in d.drive.channels.values())
    assert all(c.expiration_ms == (1_000_000 + watch.LIFETIME) * 1000 for c in channels)
    path = tmp_path / "channels.json"
    watch.save_channels(channels, path)
    assert watch.load_channels(path) == channels
    assert watch.stop_channels(d._sessions, channels) == []
    assert d.drive.channels == {}
    assert watch.stop_channels(d._sessions, channels) == []          # already gone: not a problem
    assert watch.load_channels(tmp_path / "missing.json") == []


def test_only_https_addresses_and_real_tokens_are_accepted(d, monkeypatch):
    with pytest.raises(ValueError, match="HTTPS"):
        watch.open_channels(d._sessions, "http://brain.example.com/drive/notify", TOKEN)
    monkeypatch.setenv("GDRIVE_WEBHOOK_TOKEN", "short")
    with pytest.raises(RuntimeError, match="GDRIVE_WEBHOOK_TOKEN"):
        watch.webhook_token()
    monkeypatch.setenv("GDRIVE_WEBHOOK_TOKEN", TOKEN)
    assert watch.webhook_token() == TOKEN


def test_a_signed_out_account_is_reported_when_stopping(d):
    [channel] = watch.open_channels({"priya@companya.com": d._sessions["priya@companya.com"]}, ADDRESS, TOKEN)
    assert watch.stop_channels({}, [channel]) == ["priya@companya.com: not signed in any more, its channel runs until it expires"]


# -- the receiver --------------------------------------------------------------------------------
def _receiver(d) -> tuple[webhook.Receiver, list]:
    channels = watch.open_channels(d._sessions, ADDRESS, TOKEN)
    woken: list = []
    return webhook.Receiver(TOKEN, lambda: {c.id for c in channels}, lambda: woken.append(1)), woken


def test_a_change_notification_wakes_ingestion_and_sync_does_not(d):
    receiver, woken = _receiver(d)
    assert [receiver.handle(h) for h in d.drive.notifications("sync")] == [200] * len(d._sessions)
    assert woken == []
    assert [receiver.handle(h) for h in d.drive.notifications("change")] == [200] * len(d._sessions)
    assert len(woken) == len(d._sessions)


def test_notifications_without_the_token_or_from_an_unknown_channel_are_dropped(d):
    receiver, woken = _receiver(d)
    good = d.drive.notifications("change")[0]
    for bad in ({**good, "X-Goog-Channel-Token": "x" * 32}, {k: v for k, v in good.items() if k != "X-Goog-Channel-Token"},
                {**good, "X-Goog-Channel-ID": "not-ours"}, {**good, "X-Goog-Resource-State": "exists"}, {}):
        assert receiver.handle(bad) == 404
    assert woken == [] and receiver.rejected == 5


def test_the_http_endpoint_answers_google_and_nothing_else(d):
    receiver, woken = _receiver(d)
    server = webhook.serve(receiver, 0)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        notification = d.drive.notifications("change")[0]
        assert httpx.post(base + webhook.PATH, headers=notification, content=b"").status_code == 200
        assert woken == [1]
        assert httpx.post(base + "/other", headers=notification).status_code == 404
        assert httpx.get(base + webhook.PATH, headers=notification).status_code == 404
        assert httpx.post(base + webhook.PATH, headers={**notification, "X-Goog-Channel-Token": "x"}).status_code == 404
        assert woken == [1]
    finally:
        server.shutdown()

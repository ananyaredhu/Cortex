"""Run ingestion against the simulators and the local database.

    python -m connectors.ingestion --once
    python -m connectors.ingestion --poll 5
    python -m connectors.ingestion --once --recrawl      (forget cursors; a simulator restart no longer needs it)

    python -m connectors.ingestion --once --sources confluence,jira,slack,gdrive
    python -m connectors.ingestion --poll 600 --sources confluence,jira,slack,gdrive --drive-webhook 8110 --slack-events

With --drive-webhook, Drive push notifications (connectors/gdrive/watch.py) wake the loop early to ingest Drive
alone. With --slack-events, Slack events through Socket Mode (connectors/slack/events.py) wake it to ingest the
threads they name, without a full Slack scan unless memberships or channels changed. Either way the poll still
runs every source on schedule, as the fallback when a notification or event is lost.

Environment (read from the shell, then from .env): DATABASE_URL, EMBEDDING_BACKEND (bge-m3 | none),
CONFLUENCE_SIM_URL, JIRA_SIM_URL; for Slack SLACK_BOT_TOKEN, and SLACK_APP_TOKEN for events; for Drive the OAuth client, the token files and
gdrive.local.json; for both the identity map file. DRIVE_WEBHOOK_HOST moves the Drive receiver off 127.0.0.1
(only inside a private network, behind the reverse proxy).
"""
import argparse
import json
import logging
import os
import threading
import time

from connectors.env import load_dotenv
from connectors.gdrive import DriveConnector, watch, webhook
from connectors.ingestion.embedding import from_env
from connectors.ingestion.freshness import METRIC, PIPELINE_METRIC
from connectors.ingestion.pg_store import DEFAULT_URL, PostgresStore
from connectors.ingestion.pipeline import Ingestor
from connectors.slack import SlackConnector
from connectors.slack.events import Hint, SocketModeListener
from simulators.confluence import ConfluenceConnector
from simulators.jira import JiraConnector

SOURCES = {
    "confluence": lambda: ConfluenceConnector.from_url(os.environ.get("CONFLUENCE_SIM_URL") or "http://localhost:8101"),
    "jira": lambda: JiraConnector.from_url(os.environ.get("JIRA_SIM_URL") or "http://localhost:8102"),
    "slack": SlackConnector.from_env,   # real workspace: needs SLACK_BOT_TOKEN, so it is not in the default set
    "gdrive": DriveConnector.from_env,  # real Drive: needs signed-in accounts and gdrive.local.json
}
DEFAULT_SOURCES = "confluence,jira"
# Seconds to wait after a wake-up before ingesting, so a burst becomes one pass.
SETTLE_SECONDS = {
    "gdrive": 10.0,   # Drive sends bursts, and a Google Doc's exported text was seen to lag its version counter by about 3 s (6 Oct)
    "slack": 2.0,     # one event per message, but an edit or a paste can come as several
}
TRIGGERS = {"gdrive": "drive_notification", "slack": "slack_event"}


class Wakeups:
    """Which sources a notification or event asked to ingest early. Rung from listener threads, read by the loop."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: set[str] = set()
        self.event = threading.Event()

    def ring(self, source: str) -> None:
        with self._lock:
            self._pending.add(source)
        self.event.set()

    def take(self) -> set[str]:
        with self._lock:
            pending, self._pending = self._pending, set()
            self.event.clear()
        return pending


def build_connectors(names: list[str], factories=SOURCES) -> tuple[dict, dict[str, str]]:
    """(the connectors that could be built, source -> exception class for those that could not).

    A source that is not set up (a missing token or key, an unreadable account file) is reported and left out, so the
    others keep syncing, instead of the whole process dying at start. Restart after fixing the setting."""
    built, unavailable = {}, {}
    for name in names:
        try:
            built[name] = factories[name]()
        except Exception as exc:                            # noqa: BLE001 - reported by class only: messages can hold paths
            unavailable[name] = type(exc).__name__
            logging.getLogger(__name__).warning("source %s is not set up (%s): left out until restart", name, type(exc).__name__)
    return built, unavailable


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m connectors.ingestion", description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="drain every source once and exit")
    mode.add_argument("--poll", type=float, metavar="SECONDS", help="keep draining, sleeping this long between passes")
    parser.add_argument("--sources", default=DEFAULT_SOURCES,
                        help=f"comma-separated, from: {', '.join(SOURCES)} (default: {DEFAULT_SOURCES})")
    parser.add_argument("--drive-webhook", type=int, metavar="PORT",
                        help="with --poll and gdrive: listen on 127.0.0.1:PORT for Drive push notifications")
    parser.add_argument("--slack-events", action="store_true",
                        help="with --poll and slack: receive Slack events through Socket Mode (SLACK_APP_TOKEN)")
    parser.add_argument("--recrawl", action="store_true",
                        help="forget the saved cursors and crawl from scratch. Needed after a simulator restart: "
                             "its change log starts again, so an old cursor no longer means anything")
    args = parser.parse_args()
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)   # one line per API call, with real platform IDs: too noisy

    names = [n.strip() for n in args.sources.split(",") if n.strip()]
    unknown = [n for n in names if n not in SOURCES]
    if unknown:
        parser.error(f"unknown source(s): {', '.join(unknown)}")
    if args.drive_webhook is not None and (args.once or "gdrive" not in names):
        parser.error("--drive-webhook needs --poll and gdrive in --sources")
    if args.slack_events and (args.once or "slack" not in names):
        parser.error("--slack-events needs --poll and slack in --sources")
    store = PostgresStore.connect(os.environ.get("DATABASE_URL") or DEFAULT_URL)
    if args.recrawl:
        for name in names:
            store.clear_cursor(name)
    connectors, unavailable = build_connectors(names)
    if not connectors:
        raise SystemExit(f"no source could be set up: {unavailable}")
    embedder = from_env()
    ingestor = Ingestor(connectors.values(), store, embedder)
    failed = 0
    wake = Wakeups()

    def run_pass():
        """One pass over every source, with the ones that could not be set up shown as errors too."""
        report = ingestor.run_once()
        report.errors.update(unavailable)
        return report

    server = listener = None
    if args.drive_webhook is not None and "gdrive" in connectors:
        receiver = webhook.Receiver(watch.webhook_token(), lambda: {c.id for c in watch.load_channels()}, lambda: wake.ring("gdrive"))
        # 127.0.0.1 unless the reverse proxy is on another host of a private network (a container: deploy/)
        server = webhook.serve(receiver, args.drive_webhook, host=os.environ.get("DRIVE_WEBHOOK_HOST") or "127.0.0.1")
    if args.slack_events and "slack" in connectors:
        slack = connectors["slack"]

        def on_hint(hint: Hint) -> None:
            if hint.structure:
                slack.note_structure()
            else:
                slack.note_thread(hint.channel, hint.thread_ts)
            wake.ring("slack")

        listener = SocketModeListener.from_env(on_hint).start()
    try:
        if server is not None or listener is not None:
            # The model loads on first use, which took about 90 s on 6 Oct. Pay it now, not on the first event.
            embedder.embed(["warm up"])
        first = run_pass()
        print(json.dumps(first.as_json()), flush=True)
        if args.once and first.errors:
            failed = 1                          # --once: tell the caller a source failed
        while not args.once:
            due = time.monotonic() + args.poll
            while wake.event.wait(max(0.0, due - time.monotonic())):
                pending = wake.take()
                time.sleep(max((SETTLE_SECONDS[n] for n in pending), default=0.0))
                for name in sorted(pending | wake.take()):    # and whatever arrived while settling
                    if ingestor.backing_off(name) > 0:
                        continue                # it failed recently: the scheduled pass retries it after the back-off
                    if name == "slack":
                        connectors["slack"].quick_next()
                    actions = ingestor.try_source(connectors[name], trigger="event")
                    outcome = {"actions": {name: dict(actions)}} if actions is not None \
                        else {"errors": {name: ingestor.health[name].last_error}}
                    print(json.dumps({"trigger": TRIGGERS[name], **outcome,
                                      METRIC: {name: ingestor.freshness.summary().get(name)},
                                      PIPELINE_METRIC: {name: ingestor.lag.summary().get(name)}}), flush=True)
            print(json.dumps(run_pass().as_json()), flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        if server is not None:
            server.shutdown()
        if listener is not None:
            listener.stop()
        store.close()
    raise SystemExit(failed)


if __name__ == "__main__":
    main()

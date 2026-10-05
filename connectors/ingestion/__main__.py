"""Run ingestion against the simulators and the local database.

    python -m connectors.ingestion --once
    python -m connectors.ingestion --poll 5
    python -m connectors.ingestion --once --recrawl      (after restarting a simulator)

    python -m connectors.ingestion --once --sources confluence,jira,slack

Environment (read from the shell, then from .env): DATABASE_URL, EMBEDDING_BACKEND (bge-m3 | none),
CONFLUENCE_SIM_URL, JIRA_SIM_URL, and for Slack SLACK_BOT_TOKEN plus the identity map file.
"""
import argparse
import json
import logging
import os
import time

from connectors.env import load_dotenv
from connectors.ingestion.embedding import from_env
from connectors.ingestion.pg_store import DEFAULT_URL, PostgresStore
from connectors.ingestion.pipeline import Ingestor
from connectors.slack import SlackConnector
from simulators.confluence import ConfluenceConnector
from simulators.jira import JiraConnector

SOURCES = {
    "confluence": lambda: ConfluenceConnector.from_url(os.environ.get("CONFLUENCE_SIM_URL") or "http://localhost:8101"),
    "jira": lambda: JiraConnector.from_url(os.environ.get("JIRA_SIM_URL") or "http://localhost:8102"),
    "slack": SlackConnector.from_env,   # real workspace: needs SLACK_BOT_TOKEN, so it is not in the default set
}
DEFAULT_SOURCES = "confluence,jira"


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m connectors.ingestion", description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="drain every source once and exit")
    mode.add_argument("--poll", type=float, metavar="SECONDS", help="keep draining, sleeping this long between passes")
    parser.add_argument("--sources", default=DEFAULT_SOURCES,
                        help=f"comma-separated, from: {', '.join(SOURCES)} (default: {DEFAULT_SOURCES})")
    parser.add_argument("--recrawl", action="store_true",
                        help="forget the saved cursors and crawl from scratch. Needed after a simulator restart: "
                             "its change log starts again, so an old cursor no longer means anything")
    args = parser.parse_args()
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    names = [n.strip() for n in args.sources.split(",") if n.strip()]
    unknown = [n for n in names if n not in SOURCES]
    if unknown:
        parser.error(f"unknown source(s): {', '.join(unknown)}")
    store = PostgresStore.connect(os.environ.get("DATABASE_URL") or DEFAULT_URL)
    if args.recrawl:
        for name in names:
            store.clear_cursor(name)
    ingestor = Ingestor([SOURCES[n]() for n in names], store, from_env())
    try:
        while True:
            print(json.dumps(ingestor.run_once().as_json()), flush=True)
            if args.once:
                return
            time.sleep(args.poll)
    except KeyboardInterrupt:
        pass
    finally:
        store.close()


if __name__ == "__main__":
    main()

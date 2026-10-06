"""Print the freshness report from the index (connectors/ingestion/freshness.py): per source, the last run and the
freshness and pipeline lags, p50/p95/max, overall and per trigger.

    python -m connectors.ingestion.freshness_report              # the last 24 hours
    python -m connectors.ingestion.freshness_report --hours 1
    python -m connectors.ingestion.freshness_report --since 2026-10-06T14:00:00Z   # e.g. from the start of a measurement

The JSON is the response proposed for `GET /v1/freshness`; B's API can call `report(store)` directly. Read-only.
Prints source names, times, counts and seconds only.
"""
import argparse
import json
import os

from connectors.env import load_dotenv
from connectors.ingestion.freshness import lag_seconds, summarize, utc_now, window_start
from connectors.ingestion.pg_store import DEFAULT_URL, PostgresStore
from connectors.ingestion.store import Store

DEFAULT_HOURS = 24.0


def report(store: Store, *, hours: float = DEFAULT_HOURS, now: str | None = None, since: str | None = None) -> dict:
    """Samples applied in the last `hours`, or since `since` when given (then `window_hours` is that span)."""
    now = now or utc_now()
    if since is not None:
        hours = max(0.0, lag_seconds(since, now) / 3600)
    return summarize(store.lag_since(since or window_start(now, hours)), store.runs(), now=now, window_hours=round(hours, 3))


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m connectors.ingestion.freshness_report", description=__doc__.splitlines()[0])
    parser.add_argument("--hours", type=float, default=DEFAULT_HOURS, help=f"window, in hours (default {DEFAULT_HOURS:g})")
    parser.add_argument("--since", metavar="ISO_TIME", help="only samples applied from this UTC time on, instead of --hours")
    args = parser.parse_args()
    load_dotenv()
    store = PostgresStore.connect(os.environ.get("DATABASE_URL") or DEFAULT_URL)
    try:
        print(json.dumps(report(store, hours=args.hours, since=args.since), indent=2))
    finally:
        store.close()


if __name__ == "__main__":
    main()

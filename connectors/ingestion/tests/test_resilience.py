"""A failing source is contained: the others carry on, it backs off and retries, and the store reconnects."""
import os

import psycopg
import pytest

from connectors.ingestion import FakeEmbedder, Ingestor
from connectors.ingestion.pg_store import DEFAULT_URL, PostgresStore
from connectors.ingestion.pipeline import BACKOFF_BASE, BACKOFF_MAX
from simulators.confluence.testing import SeededConfluence
from simulators.jira.testing import SeededJira


class Flaky:
    """Wraps a connector; `failing` makes every list_changes raise `error`."""

    def __init__(self, inner, error: Exception | None = None):
        self.source = inner.source
        self._inner = inner
        self.error = error
        self.calls = 0

    def list_changes(self, cursor):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self._inner.list_changes(cursor)

    def fetch(self, doc_id):
        return self._inner.fetch(doc_id)


class RateLimited(Exception):
    retry_after = 120.0


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _ingestor(store, *connectors, clock=None):
    return Ingestor(connectors, store, FakeEmbedder(), monotonic=clock or Clock(), jitter=lambda: 1.0)


def test_a_failing_source_does_not_stop_the_others(store):
    jira = Flaky(SeededJira(), ConnectionError("down"))
    confluence = SeededConfluence()
    report = _ingestor(store, jira, confluence).run_once()
    assert report.errors == {"jira": "ConnectionError"}
    assert report.count("confluence", "indexed") == 4 and store.get_cursor("jira") is None
    assert report.as_json()["errors"] == {"jira": "ConnectionError"}
    [run] = [r for r in store.runs() if r.source == "jira"]
    assert run.last_error == "ConnectionError" and run.last_ok_at is None


def test_a_failed_source_backs_off_doubling_up_to_a_cap_and_resets_on_success(store):
    clock = Clock()
    jira = Flaky(SeededJira(), ConnectionError("down"))
    ingestor = _ingestor(store, jira, clock=clock)
    delays = []
    for _ in range(7):
        ingestor.run_once()
        delays.append(ingestor.backing_off("jira"))
        skipped = ingestor.run_once()               # still backing off: not tried
        assert skipped.skipped == {"jira": pytest.approx(delays[-1])} and jira.calls == len(delays)
        clock.now += delays[-1]
    assert delays == [BACKOFF_BASE * 2 ** i for i in range(5)] + [BACKOFF_MAX, BACKOFF_MAX]
    jira.error = None
    report = ingestor.run_once()
    assert report.count("jira", "indexed") == 6 and ingestor.backing_off("jira") == 0
    jira.error = ConnectionError("down again")
    ingestor.run_once()
    assert ingestor.backing_off("jira") == BACKOFF_BASE, "a success starts the back-off over"


def test_a_rate_limit_is_waited_out(store):
    clock = Clock()
    ingestor = _ingestor(store, Flaky(SeededJira(), RateLimited()), clock=clock)
    assert ingestor.run_once().errors == {"jira": "RateLimited"}
    assert ingestor.backing_off("jira") == 120.0


def test_an_event_wake_up_respects_the_back_off(store):
    clock = Clock()
    jira = Flaky(SeededJira(), ConnectionError("down"))
    ingestor = _ingestor(store, jira, clock=clock)
    assert ingestor.try_source(jira, trigger="event") is None and jira.calls == 1
    assert ingestor.try_source(jira, trigger="event") is None and jira.calls == 1   # not even tried
    clock.now += BACKOFF_BASE
    jira.error = None
    assert ingestor.try_source(jira, trigger="event")["indexed"] == 6


def test_the_store_replaces_a_broken_connection(database_up):
    if not database_up:
        pytest.skip("Postgres is not reachable (start it with `docker compose up -d`)")
    url = os.environ.get("DATABASE_URL") or DEFAULT_URL
    store = PostgresStore.connect(url)
    try:
        store._conn.close()                              # what a database restart leaves behind
        store.reconnect_if_broken()
        assert store._conn.execute("SELECT 1").fetchone() == (1,)
        same = store._conn
        store.reconnect_if_broken()
        assert store._conn is same, "a healthy connection is kept"
    finally:
        store.close()
    plain = PostgresStore(psycopg.connect(url, autocommit=True))   # no URL: nothing to reconnect to
    plain._conn.close()
    plain.reconnect_if_broken()
    assert plain._conn.closed



@pytest.mark.parametrize("make", [SeededConfluence, SeededJira])
def test_a_simulator_reset_is_followed_by_a_crawl_that_sweeps_what_it_dropped(store, make):
    sim = make()
    ingestor = _ingestor(store, sim)
    ingestor.run_once()
    if sim.source == "confluence":
        sim.http.post("/sim/admin/pages", json={"id": "extra", "space": "ENG", "title": "Extra", "body": "planted"}).raise_for_status()
        extra = "confluence:ENG/extra"
    else:
        extra = "jira:" + sim.http.post("/sim/admin/issues", json={"project": "PAYINC", "summary": "Extra"}).json()["key"]
    ingestor.run_once()
    assert store.chunks_of(extra)
    old = store.get_cursor(sim.source)
    sim.http.post("/sim/admin/reset", json={"seed": "company_a"}).raise_for_status()   # the log starts again
    assert sim.http.get("/sim/changes", params={"cursor": old}).status_code == 410
    report = ingestor.run_once()
    assert report.errors == {} and report.count(sim.source, "deleted") == 1
    assert store.chunks_of(extra) == [] and store.get_cursor(sim.source) != old
    assert ingestor.run_once().actions[sim.source] == {}, "and then it carries on from the new cursor"

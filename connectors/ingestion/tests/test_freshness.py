"""Freshness samples and the per-source run record, on both stores, and the report built from them."""
import pytest

from connectors.base import Change, ChangeBatch
from connectors.ingestion import FakeEmbedder, Ingestor
from connectors.ingestion.embedding import NullEmbedder
from connectors.ingestion.freshness import LagSample, SourceRun, summarize
from connectors.ingestion.freshness_report import report
from simulators.confluence.testing import SeededConfluence
from simulators.jira.testing import SeededJira

RUNBOOK = "confluence:PAY/runbook-payment-service"
EDITED_AT = "2026-10-10T13:00:00Z"     # scripted event e1's own time of the edit
INDEXED_AT = "2026-10-10T13:00:42Z"


def _edited(store, *, trigger: str = "poll") -> Ingestor:
    confluence = SeededConfluence()
    ingestor = Ingestor([confluence], store, FakeEmbedder(), clock=lambda: INDEXED_AT)
    ingestor.run_once()                   # the crawl
    confluence.advance("e1")              # the owner adds a failover step at 1:00 PM
    ingestor.run_source(confluence, trigger=trigger)
    return ingestor


def test_an_edit_is_measured_from_the_sources_own_time(store):
    ingestor = _edited(store)
    assert ingestor.freshness.summary() == {"confluence": {"count": 1, "p50": 42.0, "p95": 42.0, "max": 42.0}}
    [edit] = [s for s in store.lag_since("2026-10-01T00:00:00Z") if s.trigger == "poll"]
    assert (edit.source, edit.action, edit.source_at) == ("confluence", "indexed", EDITED_AT)


def test_a_crawl_and_unchanged_changes_are_not_freshness(store):
    jira = SeededJira()
    ingestor = Ingestor([jira], store, FakeEmbedder())
    ingestor.run_once()
    ingestor.run_once()                   # nothing changed
    samples = store.lag_since("2000-01-01T00:00:00Z")
    assert {s.trigger for s in samples} == {"crawl"} and len(samples) == 6
    assert ingestor.freshness.summary() == {}


def test_a_new_embedding_model_is_not_new_content(store):
    jira = SeededJira()
    Ingestor([jira], store, FakeEmbedder()).run_once()
    reembed = Ingestor([_Replay(jira)], store, NullEmbedder())
    reembed.run_source(reembed.connectors[0], trigger="poll")
    poll = [s for s in store.lag_since("2000-01-01T00:00:00Z") if s.trigger == "poll"]
    assert len(poll) == 6 and all(s.action == "indexed" and s.source_at is None for s in poll)


def test_the_report_splits_by_trigger_and_keeps_to_its_window(store):
    _edited(store, trigger="event")
    got = report(store, hours=1, now="2026-10-10T13:30:00Z")
    confluence = got["sources"]["confluence"]
    assert confluence["freshness_lag_seconds"] == {"count": 1, "p50": 42.0, "p95": 42.0, "max": 42.0}
    assert set(confluence["by_trigger"]) == {"crawl", "event"}
    assert confluence["by_trigger"]["crawl"]["freshness_lag_seconds"]["count"] == 0
    assert (confluence["last_run_at"], confluence["last_ok_at"], confluence["last_error"]) == (INDEXED_AT, INDEXED_AT, None)
    later = report(store, hours=1, now="2026-10-10T15:00:00Z")["sources"]["confluence"]
    assert later["pipeline_lag_seconds"]["count"] == 0 and later["last_ok_at"] == INDEXED_AT
    since = report(store, now="2026-10-10T15:00:00Z", since="2026-10-10T13:00:00Z")
    assert since["window_hours"] == 2.0 and since["sources"]["confluence"]["freshness_lag_seconds"]["count"] == 1


def test_a_failed_pass_is_recorded_and_keeps_the_last_success(store):
    jira = SeededJira()
    now = ["2026-10-10T13:00:00Z"]
    ingestor = Ingestor([jira], store, FakeEmbedder(), clock=lambda: now[0])
    ingestor.run_source(jira)
    now[0] = "2026-10-10T13:05:00Z"
    with pytest.raises(ConnectionError):
        ingestor.run_source(_Broken())
    [run] = store.runs()
    assert run == SourceRun("jira", "2026-10-10T13:05:00Z", "2026-10-10T13:00:00Z", "ConnectionError", {})


def test_summarize_reports_sources_that_only_ran():
    got = summarize([], [SourceRun("slack", "2026-10-10T13:00:00Z", None, "SlackRateLimited")],
                    now="2026-10-10T13:00:05Z", window_hours=24)
    slack = got["sources"]["slack"]
    assert slack["last_error"] == "SlackRateLimited" and slack["freshness_lag_seconds"]["count"] == 0
    sample = LagSample("slack", "indexed", "event", "2026-10-10T13:00:01Z", "2026-10-10T13:00:04Z", "2026-10-10T13:00:00Z")
    lags = summarize([sample], [], now="2026-10-10T13:00:05Z", window_hours=24)["sources"]["slack"]
    assert (lags["freshness_lag_seconds"]["p50"], lags["pipeline_lag_seconds"]["p50"]) == (4.0, 3.0)


def test_an_edit_whose_source_time_goes_back_is_not_freshness(store):
    """Deleting a Slack reply takes the thread's time back to the root: no edit time to measure from."""
    jira = SeededJira()
    ingestor = Ingestor([jira], store, FakeEmbedder())
    ingestor.run_once()
    older = _Edited(jira, "jira:DBMIG-142", version="v9", updated_at="2020-01-01T00:00:00Z")
    ingestor.run_source(_Script("jira", [Change("upsert", "jira:DBMIG-142", "2026-10-10T13:00:00Z")], fetch=older.fetch))
    [sample] = [s for s in store.lag_since("2000-01-01T00:00:00Z") if s.trigger == "poll"]
    assert (sample.action, sample.source_at) == ("indexed", None)


class _Edited:
    def __init__(self, inner, doc_id, **changes):
        self._inner, self._doc_id, self._changes = inner, doc_id, changes

    def fetch(self, doc_id):
        from dataclasses import replace
        doc = self._inner.fetch(doc_id)
        return replace(doc, **self._changes) if doc_id == self._doc_id else doc


class _Replay:
    """Re-sends a connector's crawl as ordinary upserts."""

    def __init__(self, inner):
        self.source = inner.source
        self._inner = inner
        self._changes = inner.list_changes(None).changes

    def list_changes(self, cursor):
        return ChangeBatch([] if cursor == "replayed" else self._changes, "replayed", False)

    def fetch(self, doc_id):
        return self._inner.fetch(doc_id)


class _Broken:
    source = "jira"

    def list_changes(self, cursor):
        raise ConnectionError("simulator down")


def test_change_types_without_new_content_carry_no_source_time(store):
    jira = SeededJira()
    ingestor = Ingestor([jira], store, FakeEmbedder())
    ingestor.run_once()
    script = _Script("jira", [Change("principal_change", None, "2026-10-10T13:00:00Z", principal="user:x@y", token="role:a"),
                              Change("delete", "jira:DBMIG-142", "2026-10-10T13:00:00Z")])
    ingestor.run_source(script, trigger="event")
    events = [s for s in store.lag_since("2000-01-01T00:00:00Z") if s.trigger == "event"]
    assert sorted(s.action for s in events) == ["deleted", "principal_change"] and all(s.source_at is None for s in events)


class _Script:
    def __init__(self, source, changes, fetch=None):
        self.source = source
        self._changes = changes
        if fetch is not None:
            self.fetch = fetch

    def list_changes(self, cursor):
        return ChangeBatch([] if cursor == "done" else self._changes, "done", False)

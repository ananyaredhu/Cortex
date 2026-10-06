"""Freshness: how far the index trails the sources.

Two lags, both in seconds and never negative:
- `freshness_lag_seconds`: from the edit, as the source dates it (`Document.updated_at`), to the index. Content
  changes only, found incrementally (not in a full crawl, where an old document would count as years of lag).
  This is what the SLA is about: "a content edit appears in the index within 5 minutes".
- `pipeline_lag_seconds`: from when the connector saw a change to the index. Every change that altered the index,
  ACL rewrites, deletes and membership changes included. A polled source sees a change only at its next scan,
  so this leaves out the poll interval; freshness does not.

Ingestion stores a sample per change (`ingestion_lag`) and a row per source for its last run (`ingestion_sources`);
`summarize` turns them into the JSON for `GET /v1/freshness`. `python -m connectors.ingestion.freshness_report`
prints it.
"""
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

METRIC = "freshness_lag_seconds"
PIPELINE_METRIC = "pipeline_lag_seconds"
TRIGGERS = ("poll", "event", "crawl")   # what started the pass: the schedule, a notification or event, a full crawl


def utc_now() -> str:
    """Microsecond precision: two ACL snapshots of one document must not share a `valid_from`."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_ts(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def lag_seconds(detected_at: str, ingested_at: str) -> float:
    """Never negative: a source clock ahead of ours (or a scripted fixture time) counts as no lag."""
    try:
        return max(0.0, (parse_ts(ingested_at) - parse_ts(detected_at)).total_seconds())
    except ValueError:
        return 0.0


def percentile(sorted_values: list[float], pct: float) -> float:
    """Nearest-rank percentile of an ascending list."""
    if not sorted_values:
        return 0.0
    rank = max(1, -(-len(sorted_values) * pct // 100))
    return sorted_values[int(rank) - 1]


class LagTracker:
    """Recent lag samples per source, kept in process."""

    def __init__(self, window: int = 10_000) -> None:
        self._window = window
        self._samples: dict[str, deque[float]] = {}

    def record(self, source: str, seconds: float) -> None:
        self._samples.setdefault(source, deque(maxlen=self._window)).append(seconds)

    def summary(self) -> dict[str, dict[str, float]]:
        """{source: {count, p50, p95, max}} in seconds."""
        out: dict[str, dict[str, float]] = {}
        for source, samples in self._samples.items():
            values = sorted(samples)
            out[source] = {"count": len(values), "p50": percentile(values, 50), "p95": percentile(values, 95),
                           "max": values[-1] if values else 0.0}
        return out


@dataclass(frozen=True)
class LagSample:
    """One change that altered the index."""
    source: str
    action: str                  # indexed | acl_rewritten | deleted | principal_change
    trigger: str                 # one of TRIGGERS
    detected_at: str             # Change.detected_at
    applied_at: str              # when the index reflected it
    source_at: str | None = None # Document.updated_at, for new content only; None for ACL, deletes, memberships


@dataclass
class SourceRun:
    """The last pass over one source."""
    source: str
    last_run_at: str
    last_ok_at: str | None = None
    last_error: str | None = None     # exception class of the last pass when it failed; None once one succeeds
    last_actions: dict[str, int] = field(default_factory=dict)


def _stats(values: list[float]) -> dict[str, float]:
    values = sorted(values)
    return {"count": len(values), "p50": percentile(values, 50), "p95": percentile(values, 95),
            "max": values[-1] if values else 0.0}


def _lags(samples: list[LagSample]) -> dict[str, dict[str, float]]:
    fresh = [lag_seconds(s.source_at, s.applied_at) for s in samples if s.source_at and s.trigger != "crawl"]
    return {METRIC: _stats(fresh), PIPELINE_METRIC: _stats([lag_seconds(s.detected_at, s.applied_at) for s in samples])}


def window_start(now: str, hours: float) -> str:
    return (parse_ts(now) - timedelta(hours=hours)).isoformat(timespec="microseconds").replace("+00:00", "Z")


def summarize(samples: list[LagSample], runs: list[SourceRun], *, now: str, window_hours: float) -> dict:
    """The freshness report: per source, the last run and both lags over the samples given (the caller picks the
    window), overall and per trigger. Proposed as the response of `GET /v1/freshness`."""
    sources: dict[str, dict] = {}
    for name in sorted({s.source for s in samples} | {r.source for r in runs}):
        mine = [s for s in samples if s.source == name]
        run = next((r for r in runs if r.source == name), None)
        sources[name] = {
            "last_run_at": run.last_run_at if run else None,
            "last_ok_at": run.last_ok_at if run else None,
            "last_error": run.last_error if run else None,
            **_lags(mine),
            "by_trigger": {t: _lags(of) for t in TRIGGERS if (of := [s for s in mine if s.trigger == t])},
        }
    return {"as_of": now, "window_hours": window_hours, "sources": sources}

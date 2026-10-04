"""Freshness lag: how long after a change was detected at the source did the index reflect it."""
from collections import deque
from datetime import datetime, timezone

METRIC = "freshness_lag_seconds"


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

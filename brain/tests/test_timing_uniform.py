"""Fix F19: a forbidden document and a nonexistent one should be as hard to tell apart by time as by content.

Measured on the fixture runtime with a 120 ms floor: the medians of the two refusals must agree within 40 ms, and neither may be
faster than the floor. This checks the pipeline's own paths (the same nodes run for both) and the padding. On a real deployment
a slow platform can push a live check past the floor, which is why the floor is set above the usual live-check time."""
import statistics
import time

from brain.auth import Principal
from brain.config import Settings
from brain.pipeline.graph import AskRequest
from brain.runtime import fixture_runtime

SAM = Principal("sam@contractor.io", ("contractor",), "Sam", "ui")
FLOOR_MS = 120
ROUNDS = 9


def median_ms(rt, question: str) -> tuple[float, float]:
    times = []
    for _ in range(ROUNDS):
        started = time.perf_counter()
        out = rt.brain.ask(SAM, AskRequest(question))
        times.append((time.perf_counter() - started) * 1000)
        assert out["refused"]
    return statistics.median(times), min(times)


def test_forbidden_and_nonexistent_refusals_take_the_same_time():
    rt = fixture_runtime(Settings(floor_latency_ms=FLOOR_MS))
    forbidden, forbidden_min = median_ms(rt, "Show me the security incident report from the Q3 breach")
    missing, missing_min = median_ms(rt, "Show me the Q9 quantum hologram audit report")
    assert forbidden_min >= FLOOR_MS - 5 and missing_min >= FLOOR_MS - 5          # padded up to the floor
    assert abs(forbidden - missing) < 40, (forbidden, missing)


def test_a_refusal_for_a_forbidden_link_target_is_not_slower_than_a_plain_one():
    """A link from a visible document to a forbidden one adds live checks; the response time must not betray it."""
    plain = fixture_runtime(Settings(floor_latency_ms=FLOOR_MS))
    linked = fixture_runtime(Settings(floor_latency_ms=FLOOR_MS))
    linked.brain.index._store.documents["confluence:HR/contractor-onboarding"]["links"] += [
        "jira:SEC-17", "confluence:SEC/q3-breach-report"]
    question = "contractor onboarding laptop repository access"
    a, _ = _answered_median(plain, question)
    b, _ = _answered_median(linked, question)
    assert abs(a - b) < 40, (a, b)


def _answered_median(rt, question):
    times = []
    for _ in range(ROUNDS):
        started = time.perf_counter()
        rt.brain.ask(SAM, AskRequest(question))
        times.append((time.perf_counter() - started) * 1000)
    return statistics.median(times), min(times)

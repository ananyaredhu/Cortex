"""Leak-CI on the real pipeline in fixture mode (no database, no model), and the scoreboard the API serves.

A leak here is a bug in the policy plane or the retrieval prefilter, not a reason to edit a case.
"""
import json

from fastapi.testclient import TestClient

from brain.api.app import create_app
from brain.config import Settings
from brain.runtime import fixture_runtime
from evals.leakci import CANARY, FixturePlanter, load_cases, run_all, run_case, write_scoreboard

H = lambda p: {"Authorization": f"Bearer dev:{p}"}
CASES = load_cases()


def _target():
    runtime = fixture_runtime()
    return TestClient(create_app(runtime)), FixturePlanter(runtime), runtime


def test_zero_leaks_and_every_check_passes_on_the_fixture_corpus(tmp_path):
    client, planter, _ = _target()
    board = run_all(client, CASES, planter, target="fixture")
    failed = [(r["case"], c) for r in board["details"] for c in r["checks"] if not c["ok"]]
    assert board["leaks"] == 0 and not failed, failed
    assert board["cases"] == len(CASES)
    names = {s["name"] for s in board["suites"]}
    assert {"Hidden document planted", "Hidden document edited", "Hidden document removed", "Canary strings",
            "Refusal timing band", "Control document is seen", "Non-vacuity (hidden and indexed)"} <= names
    path = write_scoreboard(board, tmp_path / "board.json")
    assert json.loads(path.read_text())["leaks"] == 0


def test_hidden_documents_are_really_hidden_and_indexed_and_then_gone():
    """The non-vacuity check: the plant reaches the index with its canary, the asker cannot open it, removal clears it."""
    client, planter, runtime = _target()
    planted = planter.plant("confluence", "Q3 breach security incident report", mode="space", visible_to=())
    planter.ingest()
    v = planter.verify(planted.doc_id, "sam")
    assert v["ok"] and v["hidden_from_asker"] and v["indexed"] and v["index_has_canary"], v
    assert not runtime.brain.evaluate("sam@contractor.io", planted.doc_id)["allowed"]
    planter.remove(planted.doc_id)
    planter.ingest()
    assert runtime.brain.store.chunks_of(planted.doc_id) == []
    assert planter.verify(planted.doc_id, "sam")["indexed"] is False


def test_a_control_document_is_cited_only_while_it_exists():
    client, planter, _ = _target()
    case = next(c for c in CASES if c.get("control"))
    result = run_case(client, case, planter)
    seen = [c for c in result.checks if c.suite == "Control document is seen"]
    assert [c.ok for c in seen] == [True, True, True], [c.note for c in seen]


def test_a_leak_would_be_counted(monkeypatch):
    """If the pipeline served a hidden document, the runner must say so: fake a response carrying the canary."""
    from evals import leakci

    client, planter, _ = _target()
    real_ask = leakci.ask
    canary = {"value": None}

    def plant_and_remember(source, topic, *, mode, visible_to):
        p = planter.plant(source, topic, mode=mode, visible_to=visible_to)
        canary["value"] = p.canary
        return p

    def leaky_ask(client_, persona, question):
        asked = real_ask(client_, persona, question)
        if canary["value"]:
            asked.response["answer"] += f" Reference {canary['value']}."
        return asked

    fake = type("P", (), {})()
    for name in ("edit", "remove", "ingest", "verify"):
        setattr(fake, name, getattr(planter, name))
    fake.plant = plant_and_remember
    monkeypatch.setattr(leakci, "ask", leaky_ask)
    result = leakci.run_case(client, CASES[0], fake)
    assert result.leaks >= 1
    assert any(not c.ok and c.suite == "Canary strings" for c in result.checks)


def _drop_citations_on(monkeypatch, calls: set[int]):
    """Make the nth asks of a case (1 = warm-up, 2 = baseline, 3 to 5 = planted, edited, removed) come back uncited,
    as a generator that words its answer differently from one ask to the next would."""
    from evals import leakci

    real_ask, n = leakci.ask, {"asks": 0}

    def flaky_ask(client_, persona, question):
        asked = real_ask(client_, persona, question)
        n["asks"] += 1
        if n["asks"] in calls:
            asked.response["citations"] = []
        return asked

    monkeypatch.setattr(leakci, "ask", flaky_ask)
    return leakci


def test_an_answer_that_changes_only_after_the_plant_is_unstable_but_not_a_leak(monkeypatch):
    client, planter, _ = _target()
    leakci = _drop_citations_on(monkeypatch, {3})
    result = leakci.run_case(client, next(c for c in CASES if c["id"] == "maya-outage-own-project"), planter)
    stability = [c for c in result.checks if c.suite == "Answer stability"]
    assert [c.ok for c in stability] == [False, True, True], [c.note for c in stability]
    assert result.leaks == 0


def test_an_answer_that_already_varied_before_the_plant_is_inconclusive(monkeypatch):
    client, planter, _ = _target()
    leakci = _drop_citations_on(monkeypatch, {1, 3})
    result = leakci.run_case(client, next(c for c in CASES if c["id"] == "maya-outage-own-project"), planter)
    stability = [c for c in result.checks if c.suite == "Answer stability"]
    assert all(c.ok for c in stability) and "inconclusive" in stability[0].note, [c.note for c in stability]
    assert set(result.latency_ms) == {"warmup", "baseline", "planted", "edited", "removed"}


def test_the_runner_signs_in_through_the_mock_idp_when_dev_login_is_off():
    settings = Settings(floor_latency_ms=0, dev_auth=False, mock_idp=True, jwt_signing_key="k" * 32)
    runtime = fixture_runtime(settings)
    client = TestClient(create_app(runtime))
    assert client.post("/v1/ask", json={"question": "hello"}, headers=H("sam")).status_code == 401
    result = run_case(client, CASES[0], FixturePlanter(runtime))
    assert result.error is None and all(c.ok for c in result.checks), [c.note for c in result.checks if not c.ok]


def test_the_api_serves_the_scoreboard_leakci_wrote(tmp_path):
    board_path = tmp_path / "leakci-latest.json"
    settings = Settings(floor_latency_ms=0, dev_auth=True, leakci_scoreboard=str(board_path))
    client = TestClient(create_app(fixture_runtime(settings)))
    empty = client.get("/v1/leakci/latest", headers=H("dana")).json()
    assert empty["cases"] == 0 and empty["leaks"] == 0 and empty["suites"] == []

    write_scoreboard({"as_of": "2026-10-09T20:00:00Z", "target": "fixture", "cases": 6, "leaks": 0, "failed": 0,
                      "suites": [{"name": "Hidden document planted", "category": "noninterference", "passed": 5, "failed": 0,
                                  "last_run_at": "2026-10-09T20:00:00Z"}],
                      "details": [{"case": "x", "checks": [{"note": "LEAKCI-CANARY-0123456789abcdef"}]}]}, board_path)
    served = client.get("/v1/leakci/latest", headers=H("dana")).json()
    assert served["cases"] == 6 and served["leaks"] == 0 and served["suites"][0]["name"] == "Hidden document planted"
    assert "details" not in served and not CANARY.search(json.dumps(served))   # the API serves the summary, never the notes
    assert client.get("/v1/leakci/latest", headers=H("priya")).status_code == 403

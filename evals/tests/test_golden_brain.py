"""Golden cases and the core security properties, run against the real Brain in fixture mode (no database, no model).

A failing case here is a bug in the pipeline, not a reason to edit the fixture. The same runner (`evals/golden.py`)
runs against the deployed Brain with `SeedIds.from_manifests()` and the simulator admin endpoints as `advance`.
"""
import json

import pytest
from fastapi.testclient import TestClient

from brain.api.app import create_app
from brain.runtime import fixture_runtime
from evals.golden import run_all
from fixtures.loader import load

CASES = load()["golden"]
H = lambda p: {"Authorization": f"Bearer dev:{p}"}


@pytest.fixture
def runtime():
    return fixture_runtime()


@pytest.fixture
def client(runtime):
    return TestClient(create_app(runtime))


def test_all_golden_cases_pass_on_the_real_pipeline(client, runtime):
    results = run_all(client, CASES, lambda ev: runtime.advance(ev), runtime.reset)
    failures = {k: v for k, v in results.items() if v}
    assert not failures, failures


def test_zero_leaks_of_canaries_across_every_persona_and_question(client):
    """No persona's answer to any golden question may carry a canary from a document they cannot see."""
    data = load()
    denied_canaries = {}
    for p in data["personas"]:
        hidden = [d for d in data["documents"] if not set(d["acl"]["tokens"]) & set(p["tokens"])]
        denied_canaries[p["id"]] = [tok for d in hidden for tok in d["body"].split() if tok.startswith("CANARY-")]
    for p in data["personas"]:
        for case in CASES:
            r = client.post("/v1/ask", json={"question": case["question"]}, headers=H(p["id"])).json()
            dump = json.dumps(r)
            assert not any(c in dump for c in denied_canaries[p["id"]]), (p["id"], case["id"])


def test_three_personas_same_question_three_correct_answers(client):
    """The split-screen demo: Sam has no Slack account, so Sam's answer draws on Confluence, Jira and Drive only."""
    q = "What was the root cause of the payment outage last quarter, and what follow-up tickets were created?"
    priya = client.post("/v1/ask", json={"question": q}, headers=H("priya")).json()
    sam = client.post("/v1/ask", json={"question": q}, headers=H("sam")).json()
    assert "jira:PAYINC-9" in [c["doc_id"] for c in priya["citations"]]
    assert sam["refused"] or all(c["source"] != "slack" for c in sam["citations"])
    assert sam["coverage"]["slack"]["shown"] == 0


def test_a_link_from_a_visible_document_never_reaches_a_forbidden_one(runtime, client):
    """Sam may read the onboarding page. A link from it to the breach report and its Jira ticket must lead nowhere."""
    runtime.brain.index._store.documents["confluence:HR/contractor-onboarding"]["links"] += [
        "confluence:SEC/q3-breach-report", "jira:SEC-17"]
    dump = json.dumps(client.post("/v1/ask", json={"question": "contractor onboarding laptop repository access"},
                                  headers=H("sam")).json())
    assert "SEC-17" not in dump and "q3-breach" not in dump and "CANARY-" not in dump


def test_a_linked_document_is_reached_for_someone_who_may_open_it(client):
    r = client.post("/v1/ask", json={"question": "DBMIG-142 status"}, headers=H("priya")).json()
    linked = {c["doc_id"]: c["via_link_from"] for c in r["citations"]}
    assert linked.get("slack:C_DBMIG/thread-1") == "jira:DBMIG-142"

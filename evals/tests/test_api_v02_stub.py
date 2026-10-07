"""The api.md 0.2 additions the UI relies on, run against the stub API.

Most of these are security properties of the new fields: they must not turn into an existence signal.
"""
import json

import pytest
from fastapi.testclient import TestClient

from brain.stub_api.app import app

client = TestClient(app)
H = lambda p: {"Authorization": f"Bearer dev:{p}"}
BREACH = "Show me the security incident report from the Q3 breach"
NONEXISTENT = "Show me the Q9 quantum hologram audit report"


@pytest.fixture(autouse=True)
def fresh_state():
    client.post("/sim/reset")
    yield


def _ask(persona: str, question: str, **extra) -> dict:
    return client.post("/v1/ask", json={"question": question, **extra}, headers=H(persona)).json()


def _uniform(resp: dict) -> dict:
    return {k: v for k, v in resp.items() if k not in ("request_id", "conversation_id")}


def test_refusal_carries_every_02_field_identically_for_forbidden_and_nonexistent():
    a, b = _ask("sam", BREACH), _ask("sam", NONEXISTENT)
    assert _uniform(a) == _uniform(b)
    for field in ("coverage", "grounding", "policy_version", "unavailable_sources", "clarify"):
        assert field in a


def test_coverage_counts_only_what_is_shown():
    r = _ask("priya", "What's the status of the database migration, and were there blockers raised in Slack last week?")
    shown = sum(c["shown"] for c in r["coverage"].values())
    assert shown == len(r["citations"])
    dump = json.dumps(r).lower()
    assert "denied" not in dump and "candidates" not in dump


def test_source_filter_limits_the_search():
    r = _ask("priya", "What's the status of the database migration?", sources=["jira"])
    assert r["citations"] and {c["source"] for c in r["citations"]} == {"jira"}
    assert r["coverage"]["slack"] == {"searched": False, "shown": 0}


def test_excerpt_is_sanitized():
    r = _ask("priya", "What is the migration status page situation?")
    assert all("ignore all previous instructions" not in c["excerpt"].lower() for c in r["citations"])


def _stream(persona: str, question: str) -> list[tuple[str, dict]]:
    body = client.post("/v1/ask/stream", json={"question": question}, headers=H(persona)).text
    out = []
    for block in body.strip().split("\n\n"):
        event, data = block.split("\n")
        out.append((event.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return out


def test_stream_sends_the_same_stages_for_forbidden_and_nonexistent():
    a, b = _stream("sam", BREACH), _stream("sam", NONEXISTENT)
    assert [e for e in a if e[0] == "stage"] == [e for e in b if e[0] == "stage"]
    assert len([e for e in a if e[0] == "stage"]) == 10
    assert a[-1][0] == "result" and a[-1][1]["refused"] is True


def test_conversations_are_per_persona():
    _ask("priya", "What's the status of the database migration?")
    assert client.get("/v1/conversations", headers=H("priya")).json()["conversations"]
    assert client.get("/v1/conversations", headers=H("sam")).json()["conversations"] == []


@pytest.mark.parametrize("path", ["/v1/audit/verify", "/v1/freshness", "/v1/leakci/latest", "/v1/policy/versions"])
def test_console_endpoints_refuse_ordinary_users(path):
    assert client.get(path, headers=H("priya")).status_code == 403


def test_policy_evaluate_is_admin_only():
    body = {"user": "sam@contractor.io", "doc_id": "confluence:SEC/q3-breach-report"}
    assert client.post("/v1/policy/evaluate", json=body, headers=H("sam")).status_code == 403
    r = client.post("/v1/policy/evaluate", json=body, headers=H("dana")).json()
    assert r["allowed"] is False


def test_replay_shows_revocation_and_hides_titles_the_officer_cannot_see():
    q = "What are the open concerns in the auth service threat model?"
    rid = _ask("priya", q)["request_id"]
    client.post("/sim/advance", json={"event_id": "e2"})
    r = client.get("/v1/audit/replay", params={"request_id": rid}, headers=H("jordan")).json()
    assert {"doc_id": "slack:C_AUTHPRIV/thread-1", "change": "revoked"} in r["differences"]
    assert all("title" not in c for c in r["then"]["citations"] if c.get("restricted"))
    assert client.get("/v1/audit/replay", params={"request_id": rid}, headers=H("priya")).status_code == 403

"""The real API on the fixture runtime: golden cases, uniform refusals, the stream, the audit chain, read-through,
alerts, replay, roles. The same checks C's UI and Playwright runs rely on through api.md 0.2."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from brain.api.app import create_app
from brain.auth import DevTokens
from brain.config import Settings
from brain.runtime import fixture_runtime
from brain.tests.conftest import headers as H
from evals.golden import run_case
from fixtures.loader import load

CASES = load()["golden"]
BREACH = "Show me the security incident report from the Q3 breach"
NONEXISTENT = "Show me the Q9 quantum hologram audit report"
Q1 = "What's the status of the database migration, and were there blockers raised in Slack last week?"
RUNBOOK = "What's the latest runbook for payment-service incident failover?"
THREAT = "What are the open concerns in the auth service threat model?"


def _ask(client, persona, question, **extra):
    return client.post("/v1/ask", json={"question": question, **extra}, headers=H(persona)).json()


def _uniform(resp):
    return {k: v for k, v in resp.items() if k not in ("request_id", "conversation_id")}


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_golden_case(client, runtime, case):
    errs = run_case(client, case, lambda ev: runtime.advance(ev))
    assert not errs, errs


def test_requires_auth(client):
    assert client.post("/v1/ask", json={"question": "hello"}).status_code == 401
    assert client.post("/v1/ask", json={"question": "hello"}, headers={"Authorization": "Bearer dev:ghost"}).status_code == 401


def test_jwt_auth_when_configured():
    settings = Settings(floor_latency_ms=0, jwt_signing_key="test-key", dev_auth=False)
    client = TestClient(create_app(fixture_runtime(settings)))
    token = DevTokens("test-key").mint("priya@companya.com", ["engineer"])
    assert client.post("/v1/ask", json={"question": Q1}, headers={"Authorization": f"Bearer {token}"}).json()["citations"]
    assert client.post("/v1/ask", json={"question": Q1}, headers=H("priya")).status_code == 401
    wrong = DevTokens("other-key").mint("priya@companya.com", ["engineer"])
    assert client.post("/v1/ask", json={"question": Q1}, headers={"Authorization": f"Bearer {wrong}"}).status_code == 401


def test_forbidden_and_nonexistent_refusals_are_identical_including_02_fields(client):
    a, b = _ask(client, "sam", BREACH), _ask(client, "sam", NONEXISTENT)
    assert a["refused"] and b["refused"] and _uniform(a) == _uniform(b)
    assert a["citations"] == [] and a["claims"] == [] and a["grounding"] is None
    for field in ("coverage", "grounding", "policy_version", "unavailable_sources", "clarify", "freshness"):
        assert field in a
    assert "denied" not in json.dumps(a).lower()


def test_refusal_timing_is_padded_to_the_floor():
    client = TestClient(create_app(fixture_runtime(Settings(floor_latency_ms=120))))
    t = time.perf_counter()
    _ask(client, "sam", NONEXISTENT)
    assert time.perf_counter() - t >= 0.12


def test_coverage_counts_only_what_is_shown(client):
    r = _ask(client, "priya", Q1)
    assert sum(c["shown"] for c in r["coverage"].values()) == len(r["citations"])
    assert r["coverage"]["jira"]["searched"] and r["coverage"]["jira"]["shown"] >= 1
    assert "candidates" not in json.dumps(r).lower()


def test_source_filter_limits_the_search(client):
    r = _ask(client, "priya", "What's the status of the database migration?", sources=["jira"])
    assert r["citations"] and {c["source"] for c in r["citations"]} == {"jira"}
    assert r["coverage"]["slack"] == {"searched": False, "shown": 0}


def test_a_platform_without_an_identity_is_not_searched(client, runtime):
    """Real Slack has no account for Sam, so `resolve_identity` returns None there (the fixture connector models
    every persona on every platform, so the Slack connector is swapped for one that does not know Sam)."""
    class NoSam:
        source = "slack"

        def __init__(self, inner):
            self._inner = inner

        def resolve_identity(self, email):
            return None if email.startswith("sam@") else self._inner.resolve_identity(email)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    runtime.brain.connectors["slack"] = NoSam(runtime.brain.connectors["slack"])
    runtime.brain.resolver._connectors["slack"] = runtime.brain.connectors["slack"]
    r = _ask(client, "sam", "contractor onboarding guide")
    assert r["coverage"]["slack"] == {"searched": False, "shown": 0}
    assert r["coverage"]["confluence"]["searched"]
    priya = _ask(client, "priya", Q1)
    assert priya["coverage"]["slack"]["searched"]


def test_injection_is_sanitized_flagged_and_audited(client):
    r = _ask(client, "priya", "What is the migration status page situation?")
    dump = json.dumps(r).lower()
    assert "ignore all previous instructions" not in dump and "canary" not in dump
    assert all("ignore all previous instructions" not in c["excerpt"].lower() for c in r["citations"])
    events = client.post("/v1/audit/query", json={}, headers=H("jordan")).json()["events"]
    ask_events = [e for e in events if e["event_type"] == "ask"]
    assert "possible_injection" in ask_events[-1]["flags"]


def test_stream_sends_the_same_stages_for_forbidden_and_nonexistent(client):
    def stream(persona, question):
        body = client.post("/v1/ask/stream", json={"question": question}, headers=H(persona)).text
        out = []
        for block in body.strip().split("\n\n"):
            event, data = block.split("\n")
            out.append((event.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
        return out
    a, b = stream("sam", BREACH), stream("sam", NONEXISTENT)
    stages = [e for e in a if e[0] == "stage"]
    assert stages == [e for e in b if e[0] == "stage"] and len(stages) == 10
    assert [s[1]["stage"] for s in stages[::2]] == ["retrieve", "authorize", "verify_live", "generate", "check"]
    assert a[-1][0] == "result" and a[-1][1]["refused"] is True


def test_audit_chain_verifies_and_detects_tampering(client):
    for p in ("priya", "sam", "dana"):
        _ask(client, p, "What's the status of the database migration?")
    assert client.get("/v1/audit/verify", headers=H("jordan")).json()["ok"] is True
    client.post("/sim/tamper", params={"seq": 2})
    v = client.get("/v1/audit/verify", headers=H("jordan")).json()
    assert v["ok"] is False and v["first_broken_seq"] == 2


def test_audit_query_is_compliance_only_hashes_denied_ids_and_is_logged(client):
    _ask(client, "priya", THREAT)
    assert client.post("/v1/audit/query", json={}, headers=H("sam")).status_code == 403
    events = client.post("/v1/audit/query", json={"filter": {"decision": "allowed"}}, headers=H("jordan")).json()["events"]
    assert events and all(any(d["allowed"] for d in e["decisions"]) for e in events)
    for e in events:
        for d in e["decisions"]:
            if not d["allowed"]:
                assert "doc_id" not in d and d["doc_id_hash"].startswith("sha256:")
    again = client.post("/v1/audit/query", json={}, headers=H("jordan")).json()["events"]
    assert again[-1]["event_type"] == "audit_query" and again[-1]["actor"]["user_id"] == "jordan@companya.com"


def test_officer_gets_answer_text_only_for_sources_they_may_see(client):
    _ask(client, "dana", BREACH)
    events = client.post("/v1/audit/query", json={"filter": {"user": "dana@companya.com"}}, headers=H("jordan")).json()["events"]
    ask_events = [e for e in events if e["event_type"] == "ask"]
    assert ask_events[0]["answer"]["text"] is None and ask_events[0]["answer"]["text_withheld"] is True
    assert ask_events[0]["answer"]["sha256"].startswith("sha256:")
    assert "CANARY" not in json.dumps(events)


def test_audit_filters_by_space(client):
    _ask(client, "priya", RUNBOOK)
    _ask(client, "priya", Q1)
    pay = client.post("/v1/audit/query", json={"filter": {"user": "priya@companya.com", "space": "confluence:PAY"}},
                      headers=H("jordan")).json()["events"]
    assert pay and all(any(d.get("doc_id", "").startswith("confluence:PAY/") for d in e["decisions"]) for e in pay)


def test_explain_access_is_uniform_for_forbidden_and_missing(client):
    forbidden = client.get("/v1/explain-access", params={"doc_id": "confluence:SEC/q3-breach-report"}, headers=H("sam")).json()
    missing = client.get("/v1/explain-access", params={"doc_id": "confluence:SEC/nope"}, headers=H("sam")).json()
    assert forbidden == missing == {"found": False}
    ok = client.get("/v1/explain-access", params={"doc_id": "confluence:SEC/q3-breach-report"}, headers=H("dana")).json()
    assert ok["found"] and ok["proof_path"][0] == "user:dana@companya.com"


def test_read_through_serves_the_new_version_before_ingestion_catches_up(client, runtime):
    before = _ask(client, "priya", RUNBOOK)
    assert "failover step" not in before["answer"].lower()
    runtime.advance("e1", False)                       # the source changed; the index did not
    after = _ask(client, "priya", RUNBOOK)
    assert "failover step" in after["answer"].lower()
    assert after["freshness"]["stale_refetched"] == 1
    assert "confluence:PAY/runbook-payment-service" in [c["doc_id"] for c in after["citations"]]


def test_revocation_takes_effect_on_the_next_query(client, runtime):
    before = _ask(client, "priya", THREAT)
    assert "slack:C_AUTHPRIV/thread-1" in [c["doc_id"] for c in before["citations"]]
    runtime.advance("e2")
    after = _ask(client, "priya", THREAT)
    assert "C_AUTHPRIV" not in json.dumps(after) and "refresh token storage" not in after["answer"].lower()


def test_alerts_carry_the_question_and_skip_documents_no_longer_visible(client, runtime):
    _ask(client, "priya", RUNBOOK)
    assert client.get("/v1/alerts", headers=H("priya")).json()["alerts"] == []
    runtime.advance("e1")
    a = client.get("/v1/alerts", headers=H("priya")).json()["alerts"]
    assert a and a[0]["question"] == RUNBOOK and a[0]["changed_title"] == "Payment-service incident runbook"
    _ask(client, "priya", THREAT)
    runtime.advance("e2")
    assert "C_AUTHPRIV" not in json.dumps(client.get("/v1/alerts", headers=H("priya")).json())


def test_replay_shows_revocation_and_hides_titles_the_officer_cannot_see(client, runtime):
    rid = _ask(client, "priya", THREAT)["request_id"]
    runtime.advance("e2")
    r = client.get("/v1/audit/replay", params={"request_id": rid}, headers=H("jordan")).json()
    assert {"doc_id": "slack:C_AUTHPRIV/thread-1", "change": "revoked"} in r["differences"]
    assert all("title" not in c for c in r["then"]["citations"] if c.get("restricted"))
    assert r["then"]["answer"] is None                 # Jordan may not see the private thread
    assert client.get("/v1/audit/replay", params={"request_id": rid}, headers=H("priya")).status_code == 403


def test_conversations_are_per_persona(client):
    _ask(client, "priya", Q1)
    assert client.get("/v1/conversations", headers=H("priya")).json()["conversations"]
    assert client.get("/v1/conversations", headers=H("sam")).json()["conversations"] == []


def test_mywork_differs_per_persona_and_hides_restricted(client):
    priya = client.get("/v1/mywork", headers=H("priya")).json()
    sam = client.get("/v1/mywork", headers=H("sam")).json()
    assert priya["issues"] and not sam["issues"]
    assert all("SEC-17" not in i["doc_id"] for i in priya["issues"])
    assert "jira:DBMIG" in priya["projects"] and priya["channels"]
    assert sam["channels"] == []


@pytest.mark.parametrize("path", ["/v1/audit/verify", "/v1/freshness", "/v1/leakci/latest", "/v1/policy/versions"])
def test_console_endpoints_refuse_ordinary_users(client, path):
    assert client.get(path, headers=H("priya")).status_code == 403


def test_policy_evaluate_is_admin_only_and_logged(client):
    body = {"user": "sam@contractor.io", "doc_id": "confluence:SEC/q3-breach-report"}
    assert client.post("/v1/policy/evaluate", json=body, headers=H("sam")).status_code == 403
    r = client.post("/v1/policy/evaluate", json=body, headers=H("dana")).json()
    assert r["allowed"] is False and r["proof_path"] == []
    ok = client.post("/v1/policy/evaluate", json={"user": "dana@companya.com", "doc_id": "confluence:SEC/q3-breach-report"},
                     headers=H("dana")).json()
    assert ok["allowed"] and ok["proof_path"]
    events = client.post("/v1/audit/query", json={}, headers=H("jordan")).json()["events"]
    assert [e for e in events if e["event_type"] == "admin_view"]


def test_health_names_no_stub(client):
    h = client.get("/v1/health").json()
    assert h["ok"] and h["stub"] is False and h["generator"]

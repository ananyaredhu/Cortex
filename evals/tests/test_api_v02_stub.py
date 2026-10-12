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


def test_unavailable_source_is_skipped_and_named_the_same_way_for_everyone():
    client.post("/sim/source-status", json={"source": "slack", "status": "unavailable"})
    r = _ask("priya", "What's the status of the database migration, and were there blockers raised in Slack last week?")
    assert r["unavailable_sources"] == ["slack"]
    assert all(c["source"] != "slack" for c in r["citations"])
    assert r["coverage"]["slack"] == {"searched": False, "shown": 0}
    a, b = _ask("sam", BREACH), _ask("sam", NONEXISTENT)
    assert _uniform(a) == _uniform(b)


def test_stale_source_is_reported_per_source():
    client.post("/sim/source-status", json={"source": "confluence", "status": "stale"})
    r = _ask("priya", "What's the latest runbook for payment-service incident failover?")
    assert r["freshness"]["per_source"]["confluence"]["status"] == "stale"
    assert r["freshness"]["per_source"]["jira"]["status"] == "ok"


def _audit(persona: str, **flt) -> list[dict]:
    return client.post("/v1/audit/query", json={"filter": flt}, headers=H(persona)).json()["events"]


def test_audit_filters_by_user_space_and_decision():
    _ask("priya", "What's the latest runbook for payment-service incident failover?")
    _ask("sam", BREACH)
    pay = _audit("jordan", user="priya@companya.com", space="confluence:PAY")
    assert pay and all(e["actor"]["user_id"] == "priya@companya.com" for e in pay)
    denied = _audit("jordan", decision="denied")
    assert denied and all(any(not d["allowed"] for d in e["decisions"]) for e in denied)


def test_audit_query_is_itself_logged():
    _audit("jordan")
    events = _audit("jordan")
    assert events[-1]["event_type"] == "audit_query" and events[-1]["actor"]["user_id"] == "jordan@companya.com"


def test_officer_gets_answer_text_only_for_sources_they_may_see():
    _ask("dana", BREACH)   # cites the security-only breach report, which Jordan may not see
    ask_events = [e for e in _audit("jordan", user="dana@companya.com") if e["event_type"] == "ask"]
    assert ask_events[0]["answer"]["text"] is None and ask_events[0]["answer"]["text_withheld"] is True
    assert ask_events[0]["answer"]["sha256"].startswith("sha256:")
    assert "CANARY" not in json.dumps(ask_events)


def test_policy_evaluate_is_logged():
    client.post("/v1/policy/evaluate", json={"user": "sam@contractor.io", "doc_id": "jira:SEC-17"}, headers=H("dana"))
    assert _audit("jordan")[-1]["event_type"] == "admin_view"


def test_alerts_carry_the_question_and_skip_documents_no_longer_visible():
    q = "What's the latest runbook for payment-service incident failover?"
    _ask("priya", q)
    client.post("/sim/advance", json={"event_id": "e1"})
    a = client.get("/v1/alerts", headers=H("priya")).json()["alerts"]
    assert a[0]["question"] == q and a[0]["changed_title"] == "Payment-service incident runbook"
    # Revocation (e2) is not a content change: no alert, and nothing about the private thread.
    _ask("priya", "What are the open concerns in the auth service threat model?")
    client.post("/sim/advance", json={"event_id": "e2"})
    dump = json.dumps(client.get("/v1/alerts", headers=H("priya")).json())
    assert "C_AUTHPRIV" not in dump


def test_the_audit_question_in_words_is_planned_and_run():
    _ask("priya", "What's the latest runbook for payment-service incident failover?")
    out = client.post("/v1/audit/ask", json={"question": "What did Priya access in the PAY space in the last 30 days?"},
                      headers=H("jordan")).json()
    assert out["plan"]["kind"] == "query" and out["plan"]["filter"] == {"user": "priya@companya.com", "space": "confluence:PAY"}
    assert out["plan"]["understood"] and out["clarify"] is None and out["summary"].startswith(f"{out['result']['count']} events")
    assert out["result"]["count"] >= 1 and all(e["actor"]["user_id"] == "priya@companya.com" for e in out["result"]["events"])


def test_an_audit_question_that_names_nothing_asks_for_more_and_runs_nothing():
    out = client.post("/v1/audit/ask", json={"question": "anything odd lately?"}, headers=H("jordan")).json()
    assert out["plan"] is None and out["result"] is None and out["clarify"]


def test_the_audit_question_is_for_the_compliance_role_only():
    assert client.post("/v1/audit/ask", json={"question": "What did Priya access?"}, headers=H("sam")).status_code == 403


# -- routines (api.md 0.3): the stub serves them so the UI's Routines page works without the real Brain ------------------
ROUTINE = {"name": "Morning brief", "question": BREACH, "sources": [],
           "schedule": {"frequency": "weekdays", "weekday": None, "time": "09:00"}, "timezone": "Asia/Singapore"}


def test_routines_start_empty_and_are_created_edited_paused_and_deleted():
    assert client.get("/v1/routines", headers=H("priya")).json() == {"routines": []}
    made = client.post("/v1/routines", json=ROUTINE, headers=H("priya")).json()
    assert made["status"] == "active" and made["next_run_at"] and made["last_run"] is None
    rid = made["routine_id"]
    assert client.get("/v1/mywork", headers=H("priya")).json()["routines"][0]["routine_id"] == rid
    paused = client.patch(f"/v1/routines/{rid}", json={"status": "paused", "name": "Brief"}, headers=H("priya")).json()
    assert (paused["status"], paused["name"], paused["next_run_at"]) == ("paused", "Brief", None)
    assert client.delete(f"/v1/routines/{rid}", headers=H("priya")).json() == {"deleted": rid}
    assert client.get("/v1/routines", headers=H("priya")).json() == {"routines": []}


def test_routine_bodies_are_checked_and_the_sixth_is_refused():
    bad = [{**ROUTINE, "schedule": {"frequency": "hourly", "weekday": None, "time": "09:00"}},
           {**ROUTINE, "timezone": "Not/AZone"}, {**ROUTINE, "owner": "someone"}, {**ROUTINE, "question": " "}]
    assert [client.post("/v1/routines", json=b, headers=H("priya")).status_code for b in bad] == [422] * 4
    codes = [client.post("/v1/routines", json=ROUTINE, headers=H("priya")).status_code for _ in range(6)]
    assert codes == [200] * 5 + [409]


def test_someone_elses_routine_and_a_missing_one_answer_alike():
    rid = client.post("/v1/routines", json=ROUTINE, headers=H("priya")).json()["routine_id"]
    assert client.get("/v1/routines", headers=H("sam")).json() == {"routines": []}
    for call in (lambda i: client.patch(f"/v1/routines/{i}", json={"status": "paused"}, headers=H("sam")),
                 lambda i: client.delete(f"/v1/routines/{i}", headers=H("sam")),
                 lambda i: client.post(f"/v1/routines/{i}/run", headers=H("sam"))):
        theirs, missing = call(rid), call("rt_nope")
        assert (theirs.status_code, theirs.json()) == (missing.status_code, missing.json()) == (404, {"detail": "no such routine"})


def test_run_now_is_an_ordinary_answer_as_the_caller_and_a_refusal_reads_as_abstained():
    for persona, outcome in (("jordan", "answered"), ("sam", "abstained")):
        rid = client.post("/v1/routines", json=ROUTINE, headers=H(persona)).json()["routine_id"]
        run = client.post(f"/v1/routines/{rid}/run", headers=H(persona)).json()
        assert _uniform(run) == _uniform(_ask(persona, BREACH))
        last = client.get("/v1/routines", headers=H(persona)).json()["routines"][0]["last_run"]
        assert (last["request_id"], last["outcome"]) == (run["request_id"], outcome)

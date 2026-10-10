"""The audit agent (backlog T9): a question in words becomes a validated plan, which the compliance role's audit code runs.

The planner never sees an audit event, only people, spaces and document titles; the summary is written by code.
"""
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from brain.api.app import create_app
from brain.audit.agent import AuditPlan, Clarify, Directory, PlanError, RulePlanner, space_matches, validate_plan
from brain.auth import Principal
from brain.config import Settings
from brain.pipeline.graph import AskRequest
from brain.policy.pdp import hash_denied
from brain.runtime import fixture_runtime

JORDAN = Principal("jordan@companya.com", ("compliance",), "Jordan", "ui")
PRIYA = Principal("priya@companya.com", ("engineer",), "Priya", "ui")
DANA = Principal("dana@companya.com", ("security-lead",), "Dana", "ui")
NOW = datetime(2026, 10, 12, 9, 30, tzinfo=UTC)
H = lambda p: {"Authorization": f"Bearer dev:{p}"}   # noqa: E731
DIRECTORY = Directory(
    people={"priya@companya.com", "sam@contractor.io", "dana@companya.com"},
    spaces={"confluence:PAY", "confluence:SEC", "jira:DBMIG"},
    docs={"confluence:SEC/q3-breach-report": "Q3 security incident report: breach", "jira:DBMIG-142": "DBMIG-142 Cutover blocked",
          "confluence:PAY/runbook-payment-service": "Payment-service incident runbook"})


@pytest.fixture
def rt():
    runtime = fixture_runtime(Settings(floor_latency_ms=0, dev_auth=True))
    runtime.brain.ask(PRIYA, AskRequest("What does the payment runbook say about incidents?"))
    runtime.brain.ask(DANA, AskRequest("Show me the security incident report from the Q3 breach"))
    runtime.brain.ask(PRIYA, AskRequest("database migration blockers"))
    return runtime


def plan(question, now=NOW):
    return RulePlanner().plan(question, DIRECTORY, now)


# -- the rule planner -----------------------------------------------------------------------------------------------------------
def test_the_scenario_5_question_becomes_user_space_and_window():
    p = plan("Show me everything Priya accessed in the PAY Confluence space in the last 30 days")
    assert isinstance(p, AuditPlan) and p.kind == "query"
    assert p.filter == {"user": "priya@companya.com", "space": "confluence:PAY", "from": "2026-09-12T09:30:00Z"}


def test_a_document_named_in_words_or_by_key_is_found():
    assert plan("who accessed the Q3 breach report").filter["doc"] == "confluence:SEC/q3-breach-report"
    assert plan("who opened DBMIG-142 last week").filter == {"doc": "jira:DBMIG-142", "from": "2026-10-05T09:30:00Z"}


def test_denied_and_allowed_are_understood():
    assert plan("show denied requests by Sam yesterday").filter["decision"] == "denied"
    assert plan("what did Dana access that was allowed").filter["decision"] == "allowed"


def test_time_windows():
    assert plan("what did Priya do yesterday").filter["from"] == "2026-10-11T00:00:00Z"
    assert plan("what did Priya do yesterday").filter["to"] == "2026-10-12T00:00:00Z"
    assert plan("what did Priya do on 2026-10-05").filter == {"user": "priya@companya.com", "from": "2026-10-05T00:00:00Z",
                                                              "to": "2026-10-06T00:00:00Z"}
    assert plan("what did Priya do since 5 Oct").filter["from"] == "2026-10-05T00:00:00Z"
    assert plan("Priya's activity between Oct 1 and Oct 3").filter["to"] == "2026-10-04T00:00:00Z"
    assert plan("what did Priya do in the last 2 hours").filter["from"] == "2026-10-12T07:30:00Z"


def test_what_could_someone_see_is_a_time_travel_plan():
    p = plan("What could Priya see on 5 Oct?")
    assert p.kind == "time_travel" and p.user == "priya@companya.com" and p.at == "2026-10-05T12:00:00Z"


def test_is_the_log_intact_is_a_verify_plan():
    assert plan("Has the audit log been tampered with?").kind == "verify"


def test_what_it_cannot_tell_it_asks_about():
    assert isinstance(plan("hello there"), Clarify)
    assert isinstance(plan(""), Clarify)
    assert "one person at a time" in plan("compare Priya and Dana").message
    assert isinstance(plan("what happened on 31 Feb"), Clarify)


def test_the_planner_gets_names_not_events():
    assert set(Directory.__dataclass_fields__) == {"people", "spaces", "docs"}


# -- validation: the only door -------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("raw", [
    {"kind": "sql", "filter": {}},
    {"kind": "query", "filter": {"sql": "DROP TABLE audit_events"}},
    {"kind": "query", "filter": {"user": "stranger@evil.example"}},
    {"kind": "query", "filter": {"space": "NOPE"}},
    {"kind": "query", "filter": {"decision": "maybe"}},
    {"kind": "query", "filter": {"doc": "confluence:SEC/other"}},
    {"kind": "query", "filter": {"from": "last tuesday"}},
    {"kind": "query", "filter": {}, "user": "priya@companya.com"},
    {"kind": "time_travel", "user": "priya@companya.com"},
    {"kind": "time_travel", "at": "2026-10-05T00:00:00Z"},
    {"kind": "query", "filter": {}, "extra": 1},
])
def test_the_validator_refuses_anything_off_schema(raw):
    with pytest.raises(PlanError):
        validate_plan(raw, DIRECTORY)


def test_a_valid_plan_is_normalised():
    ok = validate_plan({"kind": "query", "filter": {"user": "PRIYA@companya.com", "space": "pay", "decision": "all"}}, DIRECTORY)
    assert ok.filter == {"user": "priya@companya.com", "space": "pay"}


# -- running it ----------------------------------------------------------------------------------------------------------------
def test_ask_runs_the_scenario_and_summarises_in_code(rt):
    out = rt.audit.ask(JORDAN, "Show me everything Priya accessed in the PAY Confluence space in the last 30 days")
    assert out["plan"]["filter"]["space"] == "confluence:PAY" and out["clarify"] is None
    assert out["result"]["count"] >= 1
    assert all(e["actor"]["user_id"] == "priya@companya.com" for e in out["result"]["events"])
    assert "events" in out["summary"] and "Documents opened" in out["summary"]


def test_who_accessed_the_breach_report(rt):
    out = rt.audit.ask(JORDAN, "who accessed the Q3 breach report")
    users = {e["actor"]["user_id"] for e in out["result"]["events"]}
    assert users == {"dana@companya.com"}


def test_a_denied_attempt_is_found_through_its_salted_hash(rt):
    salt = rt.brain.settings.denied_id_salt
    rt.audit_store.append({"request_id": "req_x", "event_type": "ask", "actor": {"user_id": "sam@contractor.io", "roles": [],
                           "client": "ui"}, "decisions": [{"doc_id_hash": hash_denied("confluence:SEC/q3-breach-report", salt),
                                                           "allowed": False, "reason": "no_access"}]})
    out = rt.audit.ask(JORDAN, "show denied attempts on the Q3 breach report")
    assert [e["actor"]["user_id"] for e in out["result"]["events"]] == ["sam@contractor.io"]
    assert "1 denied" in out["summary"]
    assert "q3-breach-report" not in str(out["result"])                          # only the hash is stored and shown


def test_a_bare_space_name_works_in_a_structured_query(rt):
    bare = rt.audit.query(JORDAN, {"filter": {"space": "PAY"}})["count"]
    full = rt.audit.query(JORDAN, {"filter": {"space": "confluence:PAY"}})["count"]
    assert bare == full >= 1                                                      # fix F14
    assert space_matches("jira:DBMIG-142", "DBMIG") and not space_matches("jira:DBMIG-142", "PAY")


def test_verify_and_time_travel_questions_run(rt):
    assert "intact" in rt.audit.ask(JORDAN, "has the log been tampered with")["summary"]
    out = rt.audit.ask(JORDAN, "what could Priya see on 2020-01-01")
    assert out["plan"]["kind"] == "time_travel" and "no record" in out["summary"]


def test_an_unplannable_question_asks_back_and_still_logs_once(rt):
    before = len([e for e in rt.audit_store.all() if e["event_type"] == "audit_query"])
    out = rt.audit.ask(JORDAN, "gibberish")
    assert out["plan"] is None and out["clarify"] and out["result"] is None
    events = [e for e in rt.audit_store.all() if e["event_type"] == "audit_query"]
    assert len(events) == before + 1 and "clarify" in events[-1]["detail"]["plan"]


def test_each_question_is_logged_once_with_its_plan(rt):
    before = len([e for e in rt.audit_store.all() if e["event_type"] == "audit_query"])
    rt.audit.ask(JORDAN, "what did Priya do in the PAY space")
    events = [e for e in rt.audit_store.all() if e["event_type"] == "audit_query"]
    assert len(events) == before + 1
    assert events[-1]["query"]["text"] == "what did Priya do in the PAY space" and events[-1]["detail"]["plan"]["kind"] == "query"
    assert rt.audit.verify()["ok"]


def test_a_planner_that_returns_nonsense_never_runs_it(rt):
    class Rogue:
        def plan(self, question, directory, now):
            return AuditPlan("query", {"user": "stranger@evil.example"})
    out = rt.audit.ask(JORDAN, "anything", planner=Rogue())
    assert out["plan"] is None and out["result"] is None and "not one the Brain knows" in out["clarify"]


def test_titles_stay_withheld_in_agent_results(rt):
    out = rt.audit.ask(JORDAN, "who accessed the Q3 breach report")
    for event in out["result"]["events"]:
        assert event["answer"]["text"] is None and event["answer"]["text_withheld"]      # Jordan may not open the breach report


# -- the endpoint and MCP ------------------------------------------------------------------------------------------------------
def test_the_endpoint_is_compliance_only(rt):
    client = TestClient(create_app(rt))
    assert client.post("/v1/audit/ask", json={"question": "what did Priya do"}, headers=H("priya")).status_code == 403
    assert client.post("/v1/audit/ask", json={}, headers=H("jordan")).status_code == 422
    ok = client.post("/v1/audit/ask", json={"question": "what did Priya do"}, headers=H("jordan"))
    assert ok.status_code == 200 and ok.json()["plan"]["filter"]["user"] == "priya@companya.com"

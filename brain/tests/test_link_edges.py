"""Link-edge expansion (backlog T4): a stored link leads to another document only if the asker may open that one too.

The fixtures link Jira DBMIG-142 to its Slack thread and to DBMIG-150; Sam (contractor) sees only the onboarding page
and the vendor notes. Tests that need a link from a visible document to a forbidden one add it to the in-memory index.
"""
import pytest

from brain.auth import Principal
from brain.config import Settings
from brain.pipeline.graph import AskRequest
from brain.runtime import fixture_runtime

PEOPLE = {
    "priya": Principal("priya@companya.com", ("engineer",), "Priya", "ui"),
    "sam": Principal("sam@contractor.io", ("contractor",), "Sam", "ui"),
}
TICKET = "DBMIG-142 status"                  # matches the Jira ticket's text, not the Slack thread's
ONBOARDING = "contractor onboarding laptop repository access"


def make(**settings):
    return fixture_runtime(Settings(floor_latency_ms=0, **settings))


def run(rt, who, question):
    return rt.brain._graph.invoke({"request_id": "r", "principal": PEOPLE[who], "request": AskRequest(question),
                                   "started": 0, "flags": []})


def link(rt, doc_id, *targets):
    rt.brain.index._store.documents[doc_id]["links"].extend(targets)


def ids(candidates):
    return [c.doc_id for c in candidates]


# -- reaching a document through a link --------------------------------------------------------------------------
def test_a_linked_document_is_reached_when_the_keywords_miss_it():
    state = run(make(), "priya", TICKET)
    assert ids(state["candidates"]) == ["jira:DBMIG-142"]                       # the search alone finds only the ticket
    assert state["linked"]["slack:C_DBMIG/thread-1"] == "jira:DBMIG-142"
    assert "slack:C_DBMIG/thread-1" in ids(state["allowed"])


def test_the_answer_cites_the_linked_thread_and_says_which_document_led_to_it():
    rt = make()
    response = rt.brain.ask(PEOPLE["priya"], AskRequest(TICKET))
    by_id = {c["doc_id"]: c for c in response["citations"]}
    slack = by_id.get("slack:C_DBMIG/thread-1")
    assert slack is not None and slack["via_link_from"] == "jira:DBMIG-142"
    assert by_id["jira:DBMIG-142"]["via_link_from"] is None
    assert slack["why_visible"]                                                   # a live proof path, like any citation


def test_a_linked_document_ranks_below_the_hit_that_linked_to_it():
    state = run(make(), "priya", TICKET)
    scores = {c.doc_id: c.score for c in state["allowed"]}
    assert scores["slack:C_DBMIG/thread-1"] == pytest.approx(scores["jira:DBMIG-142"] * 0.5)


def test_it_is_one_hop_only():
    rt = make()
    link(rt, "jira:DBMIG-150", "confluence:PAY/runbook-payment-service")         # two hops from the ticket
    state = run(rt, "priya", TICKET)
    assert "confluence:PAY/runbook-payment-service" not in ids(state["allowed"])


def test_expansion_can_be_switched_off():
    state = run(make(link_expansion=False), "priya", TICKET)
    assert ids(state["allowed"]) == ["jira:DBMIG-142"] and state["linked"] == {}


# -- a link never grants access ---------------------------------------------------------------------------------
def test_a_forbidden_target_is_not_reached_through_a_visible_document():
    rt = make()
    link(rt, "confluence:HR/contractor-onboarding", "jira:SEC-17", "confluence:SEC/q3-breach-report")
    state = run(rt, "sam", ONBOARDING)
    assert ids(state["allowed"]) == ["confluence:HR/contractor-onboarding"]
    assert state["linked"] == {}


def test_a_forbidden_target_leaves_no_trace_in_the_response():
    clean = make().brain.ask(PEOPLE["sam"], AskRequest(ONBOARDING))
    rt = make()
    link(rt, "confluence:HR/contractor-onboarding", "jira:SEC-17", "confluence:SEC/q3-breach-report")
    linked = rt.brain.ask(PEOPLE["sam"], AskRequest(ONBOARDING))
    for key in ("answer", "claims", "citations", "refused", "abstained", "coverage", "freshness"):
        assert linked[key] == clean[key] or key == "freshness"
    assert "SEC-17" not in str(linked) and "breach" not in str(linked).lower()
    assert linked["freshness"]["stale_refetched"] == clean["freshness"]["stale_refetched"]


def test_a_missing_target_looks_like_a_forbidden_one():
    rt = make()
    link(rt, "confluence:HR/contractor-onboarding", "jira:NOPE-1", "confluence:NOWHERE/missing")
    forbidden = make()
    link(forbidden, "confluence:HR/contractor-onboarding", "jira:SEC-17", "confluence:SEC/q3-breach-report")
    a = rt.brain.ask(PEOPLE["sam"], AskRequest(ONBOARDING))
    b = forbidden.brain.ask(PEOPLE["sam"], AskRequest(ONBOARDING))
    assert {k: a[k] for k in ("answer", "claims", "citations", "refused", "coverage")} == \
           {k: b[k] for k in ("answer", "claims", "citations", "refused", "coverage")}


def test_a_forbidden_target_still_feeds_the_leak_scan_and_the_audit_hashes_it():
    rt = make()
    link(rt, "confluence:HR/contractor-onboarding", "jira:SEC-17")
    state = run(rt, "sam", ONBOARDING)
    assert "jira:SEC-17" in {d.doc_id for d in state["denied_docs"]}
    rt.brain.ask(PEOPLE["sam"], AskRequest(ONBOARDING))
    event = [e for e in rt.audit_store.all() if e["event_type"] == "ask"][-1]
    assert "SEC-17" not in str(event["decisions"])                               # denied IDs are salted hashes
    assert any(not d["allowed"] for d in event["decisions"])


def test_nothing_is_followed_from_a_question_with_no_allowed_hit():
    rt = make()
    state = run(rt, "sam", "security incident report Q3 breach")
    assert state["allowed"] == [] and state["linked"] == {}


def test_a_source_the_asker_is_not_searching_is_not_followed():
    rt = make()
    response = rt.brain.ask(PEOPLE["priya"], AskRequest(TICKET, sources=["jira"]))
    assert {c["source"] for c in response["citations"]} == {"jira"}


# -- bounds ---------------------------------------------------------------------------------------------------------
def test_caps_per_source_and_in_total():
    one_each = run(make(link_per_source=1), "priya", TICKET)["linked"]
    assert sorted(d.split(":")[0] for d in one_each) == ["jira", "slack"]          # one Jira and one Slack target
    two_jira = make(link_per_source=1)
    link(two_jira, "jira:DBMIG-142", "jira:DBMIG-999")
    assert len([d for d in run(two_jira, "priya", TICKET)["linked"] if d.startswith("jira")]) == 1
    assert len(run(make(link_total=1), "priya", TICKET)["linked"]) == 1


def test_the_check_budget_bounds_how_many_targets_are_looked_at():
    assert len(run(make(link_checks=1), "priya", TICKET)["linked"]) == 1
    assert run(make(link_checks=0), "priya", TICKET)["linked"] == {}


def test_the_resolver_translates_a_stored_link_into_the_real_id():
    rt = make()
    rt.brain.link_resolver = lambda doc_id: {"slack:C_ALIAS/thread": "slack:C_DBMIG/thread-1"}.get(doc_id, doc_id)
    link(rt, "jira:DBMIG-150", "slack:C_ALIAS/thread")
    state = run(rt, "priya", "DBMIG-150")
    assert state["linked"].get("slack:C_DBMIG/thread-1") in {"jira:DBMIG-150", "jira:DBMIG-142"}


# -- audit ----------------------------------------------------------------------------------------------------------
def test_the_audit_event_records_which_links_were_followed():
    rt = make()
    rt.brain.ask(PEOPLE["priya"], AskRequest(TICKET))
    event = [e for e in rt.audit_store.all() if e["event_type"] == "ask"][-1]
    assert {"doc_id": "slack:C_DBMIG/thread-1", "via_link_from": "jira:DBMIG-142"} in event["links_followed"]
    assert rt.audit.verify()["ok"]

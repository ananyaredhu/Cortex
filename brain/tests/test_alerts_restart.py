"""Fix F16: My Work alerts are rebuilt from the audit log, so they survive a restart of the Brain."""
from brain.auth import Principal
from brain.config import Settings
from brain.pipeline.graph import AskRequest, Brain
from brain.runtime import fixture_runtime

PRIYA = Principal("priya@companya.com", ("engineer",), "Priya", "ui")
SAM = Principal("sam@contractor.io", ("contractor",), "Sam", "ui")
RUNBOOK = "What's the latest runbook for payment-service incident failover?"


def restarted(rt) -> Brain:
    b = rt.brain
    return Brain(b.settings, b.connectors, b.index, b.store, None, b.generator, rt.audit_store)


def test_an_alert_survives_a_restart():
    rt = fixture_runtime(Settings(floor_latency_ms=0, dev_auth=True))
    rt.brain.ask(PRIYA, AskRequest(RUNBOOK))
    assert rt.brain.alerts(PRIYA) == []                                       # nothing changed yet
    rt.advance("e1")                                                          # the owner edits the runbook
    before = rt.brain.alerts(PRIYA)
    assert before and before[0]["question"] == RUNBOOK and before[0]["changed_doc"] == "confluence:PAY/runbook-payment-service"
    after = restarted(rt).alerts(PRIYA)
    assert after == before


def test_a_question_asked_before_the_restart_is_alerted_after_it():
    rt = fixture_runtime(Settings(floor_latency_ms=0, dev_auth=True))
    rt.brain.ask(PRIYA, AskRequest(RUNBOOK))
    fresh = restarted(rt)                                                     # the process restarted before the edit
    rt.advance("e1")
    assert [a["changed_doc"] for a in fresh.alerts(PRIYA)] == ["confluence:PAY/runbook-payment-service"]


def test_alerts_stay_private_to_the_person_who_asked():
    rt = fixture_runtime(Settings(floor_latency_ms=0, dev_auth=True))
    rt.brain.ask(PRIYA, AskRequest(RUNBOOK))
    rt.advance("e1")
    assert restarted(rt).alerts(SAM) == []


def test_an_alert_for_a_document_the_person_lost_is_not_shown():
    rt = fixture_runtime(Settings(floor_latency_ms=0, dev_auth=True))
    rt.brain.ask(PRIYA, AskRequest("What are the open concerns in the auth service threat model?"))
    rt.advance("e2")                                                          # Priya leaves the private auth channel
    assert all("AUTHPRIV" not in a["changed_doc"] for a in restarted(rt).alerts(PRIYA))


def test_a_refused_question_makes_no_alert():
    rt = fixture_runtime(Settings(floor_latency_ms=0, dev_auth=True))
    rt.brain.ask(SAM, AskRequest("Show me the security incident report from the Q3 breach"))
    rt.advance("e1")
    assert restarted(rt).alerts(SAM) == []

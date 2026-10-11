"""Fix F9: when the live version check fails, the indexed copy is still served, but the answer says it was not checked."""
import pytest

from brain.auth import Principal
from brain.config import Settings
from brain.pipeline.graph import AskRequest
from brain.runtime import fixture_runtime

PRIYA = Principal("priya@companya.com", ("engineer",), "Priya", "ui")
Q1 = "What's the status of the database migration, and were there blockers raised in Slack last week?"


@pytest.fixture
def rt():
    return fixture_runtime(Settings(floor_latency_ms=0))


def break_versions(rt, source: str):
    """Make one source's live version check fail, as a timeout or a 500 from the platform would."""
    connector = rt.brain.connectors[source]

    def boom(doc_id):
        raise TimeoutError("platform did not answer")
    connector.version = boom


def test_a_normal_answer_is_verified(rt):
    out = rt.brain.ask(PRIYA, AskRequest(Q1))
    assert out["freshness"]["unverified_sources"] == [] and all(c["unverified"] is False for c in out["citations"])
    assert all(v["status"] != "unverified" for v in out["freshness"]["per_source"].values())


def test_a_failed_check_still_serves_the_indexed_copy_but_flags_it(rt):
    break_versions(rt, "jira")
    out = rt.brain.ask(PRIYA, AskRequest(Q1))
    by_id = {c["doc_id"]: c for c in out["citations"]}
    assert not out["refused"] and by_id["jira:DBMIG-142"]["unverified"] is True
    assert by_id["slack:C_DBMIG/thread-1"]["unverified"] is False                  # only the document whose check failed
    assert out["freshness"]["unverified_sources"] == ["jira"]
    assert out["freshness"]["per_source"]["jira"]["status"] == "unverified"
    assert out["freshness"]["per_source"]["slack"]["status"] != "unverified"


def test_the_flag_reaches_the_model_and_the_audit_log(rt):
    from brain.gateway.models import TemplateGenerator

    class Spy:
        model = "spy"
        packets: list = []

        def generate(self, packet):
            self.packets.append(packet)
            return TemplateGenerator().generate(packet)
    rt.brain.generator = Spy()
    break_versions(rt, "jira")
    rt.brain.ask(PRIYA, AskRequest(Q1))
    flagged = [e for e in Spy.packets[-1]["evidence"] if "freshness_unverified" in e["flags"]]
    assert flagged and all(e["source"] == "jira" for e in flagged)
    event = [e for e in rt.audit_store.all() if e["event_type"] == "ask"][-1]
    assert "freshness_unverified" in event["flags"]


def test_a_document_that_vanished_is_dropped_not_served_unverified(rt):
    from connectors.base import DocumentNotFound
    connector = rt.brain.connectors["jira"]

    def gone(doc_id):
        raise DocumentNotFound(doc_id)
    connector.version = gone
    out = rt.brain.ask(PRIYA, AskRequest(Q1))
    assert all(c["source"] != "jira" for c in out["citations"])


def test_a_failed_check_changes_nothing_about_what_the_person_may_see(rt):
    """Freshness is about the text, never about access: a refusal is still a refusal."""
    break_versions(rt, "confluence")
    sam = Principal("sam@contractor.io", ("contractor",), "Sam", "ui")
    out = rt.brain.ask(sam, AskRequest("Show me the security incident report from the Q3 breach"))
    assert out["refused"] and out["citations"] == [] and out["freshness"]["unverified_sources"] == []

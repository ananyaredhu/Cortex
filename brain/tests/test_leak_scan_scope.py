"""Fix F5: the leak scan compares an answer with every document the asker cannot see, not only the candidates the live check
refused (the prefilter removes forbidden documents first, so that set was almost always empty)."""
import pytest

from brain.auth import Principal
from brain.config import Settings
from brain.gateway.models import Generated
from brain.pipeline.graph import AskRequest
from brain.runtime import fixture_runtime

SAM = Principal("sam@contractor.io", ("contractor",), "Sam", "ui")
PRIYA = Principal("priya@companya.com", ("engineer",), "Priya", "ui")
ONBOARDING = "contractor onboarding laptop repository access"
BREACH_DOC = "confluence:SEC/q3-breach-report"
CANARY = "CANARY-7f3a-Q3BREACH"


class Leaky:
    """A generator that goes wrong: it cites a document Sam may read but writes content from one he may not."""
    model = "leaky"

    def __init__(self, text: str) -> None:
        self.text = text

    def generate(self, packet):
        doc = packet["evidence"][0]["doc_id"] if packet["evidence"] else "x"
        return Generated(self.text, [{"text": self.text, "citations": [doc]}], model=self.model)


@pytest.fixture
def rt():
    return fixture_runtime(Settings(floor_latency_ms=0, dev_auth=True))


def last(rt):
    return [e for e in rt.audit_store.all() if e["event_type"] == "ask"][-1]


def test_the_denied_set_holds_every_document_the_asker_cannot_see(rt):
    state = rt.brain._graph.invoke({"request_id": "r", "principal": SAM, "request": AskRequest(ONBOARDING), "started": 0, "flags": []})
    denied = {d.doc_id for d in state["denied_docs"]}
    assert {BREACH_DOC, "jira:SEC-17", "slack:C_DBMIGPRIV/thread-1"} <= denied
    assert "confluence:HR/contractor-onboarding" not in denied                       # a document Sam may read is not in it


def test_a_leak_through_an_allowed_citation_is_caught(rt):
    rt.brain.generator = Leaky(f"Contractor onboarding is simple. {CANARY} The breach exposed an internal admin token for 3 hours.")
    out = rt.brain.ask(SAM, AskRequest(ONBOARDING))
    assert out["abstained"] and out["claims"] == [] and CANARY not in str(out) and "admin token" not in str(out)
    event = last(rt)
    assert event["checks"]["leak_scan"] == "hit" and len(event["checks"]["leak_hits"]) >= 1
    assert BREACH_DOC not in str(event) and "SEC-17" not in str(event)               # hits are salted hashes in the log


def test_without_the_hidden_documents_the_same_leak_gets_through(rt):
    """The negative control: this is what the scan did before the fix, when only refused candidates were compared."""
    rt.brain.generator = Leaky(f"Contractor onboarding is simple. {CANARY}")
    rt.brain.index.documents_hidden = lambda tokens: []
    out = rt.brain.ask(SAM, AskRequest(ONBOARDING))
    assert CANARY in str(out["claims"])                                              # the leak was invisible to the scan


def test_a_paraphrase_sharing_a_long_run_of_words_is_caught_and_a_clean_answer_is_not(rt):
    rt.brain.generator = Leaky("Onboarding: leaked credential in a build log was used; the breach exposed an internal admin "
                               "token for 3 hours before detection.")
    assert rt.brain.ask(SAM, AskRequest(ONBOARDING))["abstained"]
    rt.brain.generator = Leaky("Contractors get a laptop, request repository access and follow the code review rules.")
    clean = rt.brain.ask(SAM, AskRequest(ONBOARDING))
    assert not clean["abstained"] and last(rt)["checks"]["leak_scan"] == "clean"


def test_the_log_says_how_many_documents_the_scan_covered(rt):
    rt.brain.ask(SAM, AskRequest(ONBOARDING))
    assert last(rt)["checks"]["leak_scan_docs"] >= 10
    rt.brain.ask(PRIYA, AskRequest("database migration blockers"))
    assert 0 < last(rt)["checks"]["leak_scan_docs"] < 18


def test_a_forbidden_document_that_was_a_candidate_is_still_scanned(rt):
    """A document the prefilter let through and the live check refused is added to the hidden set, not replaced by it."""
    asker = rt.brain.resolver.resolve("priya@companya.com")
    denied = rt.brain._denied_set(asker, ["slack:C_DBMIGPRIV/thread-1"])
    assert "slack:C_DBMIGPRIV/thread-1" in {d.doc_id for d in denied}


def test_prepared_entries_are_reused_until_the_document_changes(rt):
    asker = rt.brain.resolver.resolve("sam@contractor.io")
    first = rt.brain._denied_set(asker, [])
    second = rt.brain._denied_set(asker, [])
    assert all(a is b for a, b in zip(first, second, strict=True)) and first[0].shingles is not None

"""Router rewrite and conversation memory (backlog T6): a follow-up is understood from the earlier turns, and memory never
carries anything the person may no longer see."""
import pytest

from brain.auth import Principal
from brain.config import Settings
from brain.gateway.models import TemplateGenerator
from brain.pipeline.graph import AskRequest
from brain.pipeline.rewrite import is_follow_up, rewrite_query
from brain.runtime import fixture_runtime

PRIYA = Principal("priya@companya.com", ("engineer",), "Priya", "ui")
SAM = Principal("sam@contractor.io", ("contractor",), "Sam", "ui")
Q1 = "What's the status of the database migration, and were there blockers raised in Slack last week?"
THREAT = "What are the open concerns in the auth service threat model?"


class Spy:
    model = "spy"

    def __init__(self):
        self.packets, self._inner = [], TemplateGenerator()

    def generate(self, packet):
        self.packets.append(packet)
        return self._inner.generate(packet)


@pytest.fixture
def rt():
    runtime = fixture_runtime(Settings(floor_latency_ms=0, dev_auth=True))
    runtime.brain.generator = Spy()
    return runtime


def last_ask(rt):
    return [e for e in rt.audit_store.all() if e["event_type"] == "ask"][-1]


def ids(response):
    return [c["doc_id"] for c in response["citations"]]


# -- the rewrite on its own ---------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("question", ["the second blocker?", "why?", "who raised it?", "what about Dana?", "and the third one?",
                                      "tell me more about those"])
def test_these_are_follow_ups(question):
    assert is_follow_up(question)


@pytest.mark.parametrize("question", ["and what about the payment outage?", "What is the status of the database migration?",
                                      "Show me the Q3 breach report", "who owns the runbook for payment failover"])
def test_these_stand_on_their_own(question):
    assert not is_follow_up(question)


def test_a_rewrite_needs_a_history_and_a_follow_up():
    history = [{"question": "database migration blockers"}]
    assert rewrite_query("the second blocker?", []) == ("the second blocker?", False)
    assert rewrite_query("payment outage root cause", history) == ("payment outage root cause", False)
    assert rewrite_query("the second blocker?", history) == ("database migration blockers the second blocker?", True)


def test_a_chain_of_follow_ups_keeps_the_topic():
    history = [{"question": "the second blocker?", "query": "database migration blockers the second blocker?"}]
    assert rewrite_query("and the third one?", history)[0].startswith("database migration blockers")


def test_the_rewritten_query_is_capped():
    assert len(rewrite_query("why?", [{"question": "x " * 600}])[0]) <= 400


# -- in the pipeline ---------------------------------------------------------------------------------------------------------
def test_a_follow_up_searches_with_the_topic_of_the_conversation(rt):
    first = rt.brain.ask(PRIYA, AskRequest(Q1))
    rt.brain.ask(PRIYA, AskRequest("why?", conversation_id=first["conversation_id"]))
    assert last_ask(rt)["query"]["rewritten"].startswith("What's the status of the database migration")
    assert last_ask(rt)["query"]["text"] == "why?"                               # the log keeps what the person typed
    alone = rt.brain.ask(PRIYA, AskRequest("why?"))
    assert alone["refused"] and last_ask(rt)["query"]["rewritten"] is None       # the same words with no conversation find nothing
    with_memory = rt.brain.ask(PRIYA, AskRequest("why?", conversation_id=first["conversation_id"]))
    assert not with_memory["refused"] and "jira:DBMIG-142" in ids(with_memory)


def test_a_new_topic_in_the_same_conversation_is_not_dragged_back(rt):
    first = rt.brain.ask(PRIYA, AskRequest(Q1))
    out = rt.brain.ask(PRIYA, AskRequest("and what about the payment outage?", conversation_id=first["conversation_id"]))
    assert last_ask(rt)["query"]["rewritten"] is None
    assert "jira:PAYINC-9" in ids(out) and "jira:DBMIG-142" not in ids(out)


def test_the_model_gets_the_earlier_turns_but_they_are_never_evidence(rt):
    first = rt.brain.ask(PRIYA, AskRequest(Q1))
    out = rt.brain.ask(PRIYA, AskRequest("the second blocker?", conversation_id=first["conversation_id"]))
    packet = rt.brain.generator.packets[-1]
    assert [h["question"] for h in packet["history"]] == [Q1] and packet["history"][0]["answer"]
    assert "history" in __import__("brain.gateway.models", fromlist=["system_prompt"]).system_prompt(packet)
    evidence_ids = {e["doc_id"] for e in packet["evidence"]}
    assert set(ids(out)) <= evidence_ids                                          # citations come from this turn's evidence only


def test_only_the_last_few_turns_are_remembered():
    runtime = fixture_runtime(Settings(floor_latency_ms=0, memory_turns=2))
    runtime.brain.generator = Spy()
    first = runtime.brain.ask(PRIYA, AskRequest(Q1))
    cid = first["conversation_id"]
    for q in ("database migration cutover blockers", "replica lag in the migration", "payment outage root cause"):
        runtime.brain.ask(PRIYA, AskRequest(q, conversation_id=cid))
    runtime.brain.ask(PRIYA, AskRequest("why?", conversation_id=cid))
    assert len(runtime.brain.generator.packets[-1]["history"]) == 2


def test_memory_can_be_switched_off():
    runtime = fixture_runtime(Settings(floor_latency_ms=0, memory_turns=0))
    first = runtime.brain.ask(PRIYA, AskRequest(Q1))
    assert runtime.brain.ask(PRIYA, AskRequest("why?", conversation_id=first["conversation_id"]))["refused"]


def test_someone_elses_conversation_gives_no_memory_and_looks_like_none(rt):
    first = rt.brain.ask(PRIYA, AskRequest(Q1))
    theirs = rt.brain.ask(SAM, AskRequest("why?", conversation_id=first["conversation_id"]))
    nobody = rt.brain.ask(SAM, AskRequest("why?", conversation_id="c_doesnotexist"))
    assert theirs["refused"] and last_ask(rt)["query"]["rewritten"] is None
    assert {k: theirs[k] for k in ("answer", "claims", "citations", "refused", "coverage")} == \
           {k: nobody[k] for k in ("answer", "claims", "citations", "refused", "coverage")}


def test_a_refused_turn_is_not_remembered(rt):
    refused = rt.brain.ask(PRIYA, AskRequest("Show me the Q9 quantum hologram audit report"))
    assert refused["refused"]
    out = rt.brain.ask(PRIYA, AskRequest("why?", conversation_id=refused["conversation_id"]))
    assert out["refused"] and last_ask(rt)["query"]["rewritten"] is None


def test_a_turn_whose_source_was_revoked_is_dropped_from_memory(rt):
    first = rt.brain.ask(PRIYA, AskRequest(THREAT))
    assert "slack:C_AUTHPRIV/thread-1" in ids(first)                              # Priya may read the private channel today
    cid = first["conversation_id"]
    assert rt.brain.generator.packets                                             # sanity: the model ran
    rt.advance("e2")                                                              # scripted event: Priya leaves the private channel
    out = rt.brain.ask(PRIYA, AskRequest("who raised the first concern?", conversation_id=cid))
    assert rt.brain.generator.packets[-1]["history"] == []                        # the only earlier turn cited the revoked channel
    assert last_ask(rt)["query"]["rewritten"] is None
    assert "slack:C_AUTHPRIV/thread-1" not in ids(out)


def test_memory_never_widens_access(rt):
    """A follow-up built from a question about a forbidden document finds nothing new, and looks like any other refusal."""
    sam_first = rt.brain.ask(SAM, AskRequest("Show me the security incident report from the Q3 breach"))
    out = rt.brain.ask(SAM, AskRequest("the second one?", conversation_id=sam_first["conversation_id"]))
    assert out["refused"] and out["citations"] == []

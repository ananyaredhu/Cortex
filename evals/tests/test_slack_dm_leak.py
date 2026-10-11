"""Security properties of Slack DMs, through the real policy plane (docs/02-contracts/acl-model.md, "Slack DMs").

The Slack connector runs over the in-memory Slack, real ingestion writes the index, and the Brain's own identity
resolver and PDP decide. A fact stated only in a DM between Priya and Dana must be unreachable for everyone else
at both gates, the prefilter and the just-in-time check, and a refusal must not tell a DM that exists from one
that does not.
"""
import pytest

from brain.policy.identity import IdentityResolver
from brain.policy.pdp import PDP
from connectors.ingestion import FakeEmbedder, Ingestor, InMemoryStore
from connectors.slack.tests.test_slack_dms import SECRET, DmWorld
from fixtures.loader import load

PERSONAS = {p["id"]: p["email"] for p in load()["personas"]}
NO_SUCH_DM = "slack:D0NOSUCHDM/1791619200.000100"
SALT = "test-salt"


class Brain:
    """The policy plane over one Slack workspace: who the asker is, what the prefilter lets through, what the PDP says."""

    def __init__(self) -> None:
        self.w = DmWorld()                                      # Priya, Dana and Maya have authorized DMs
        self.doc_id = self.w.dm("priya", "dana")
        self.store = InMemoryStore()
        self.ingestor = Ingestor([self.w.conn], self.store, FakeEmbedder())
        self.ingestor.run_once()
        self.resolver = IdentityResolver({"slack": self.w.conn}, ttl_s=0)
        self.pdp = PDP({"slack": self.w.conn}, policy_version="test", ttl_s=0)

    def prefilter(self, persona: str) -> bool:
        """`acl_tokens && asker_tokens` on the DM's chunks: would retrieval even see them?"""
        tokens = self.resolver.resolve(PERSONAS[persona]).tokens
        return any(set(chunk.acl_tokens) & tokens for chunk in self.store.chunks_of(self.doc_id))

    def decide(self, persona: str, doc_id: str | None = None):
        return self.pdp.check(self.resolver.resolve(PERSONAS[persona]), doc_id or self.doc_id)


@pytest.fixture
def brain() -> Brain:
    return Brain()


def test_the_dm_is_indexed_and_its_participants_reach_it(brain):
    assert SECRET in brain.store.chunks_of(brain.doc_id)[0].text
    for persona in ("priya", "dana"):
        decision = brain.decide(persona)
        assert brain.prefilter(persona) and decision.allowed and decision.proof_path == (f"user:{PERSONAS[persona]}",)


@pytest.mark.parametrize("persona", [p for p in PERSONAS if p not in ("priya", "dana")])
def test_nobody_else_reaches_it_at_either_gate(brain, persona):
    """Maya has authorized her own DMs, Jordan holds the audit role, Sam has no Slack account: none of it helps."""
    assert not brain.prefilter(persona)
    assert not brain.decide(persona).allowed


@pytest.mark.parametrize("persona", ["maya", "jordan"])
def test_a_refusal_does_not_tell_a_dm_that_exists_from_one_that_does_not(brain, persona):
    denied, nothing = brain.decide(persona), brain.decide(persona, NO_SUCH_DM)
    shape = lambda d: (d.allowed, d.proof_path, d.acl_snapshot_hash, d.policy_version, d.reason)   # noqa: E731
    assert shape(denied) == shape(nothing) == (False, (), "", "test", "not_found")
    logged, logged_nothing = denied.audit_view(SALT), nothing.audit_view(SALT)
    assert {k: v for k, v in logged.items() if k != "doc_id_hash"} == {k: v for k, v in logged_nothing.items() if k != "doc_id_hash"}
    assert brain.doc_id not in str(logged)


def test_withdrawing_takes_the_dm_from_both_participants(brain):
    brain.w.slack.revoked.add(brain.w.user("dana"))             # Dana removes the app in Slack
    assert not brain.decide("dana").allowed                     # the live check, before ingestion runs again
    brain.ingestor.run_once()
    assert brain.store.chunks_of(brain.doc_id) == []
    assert not brain.prefilter("priya") and not brain.decide("priya").allowed

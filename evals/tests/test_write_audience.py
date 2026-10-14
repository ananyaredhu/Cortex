"""No write widens an audience (AGENTS rule 8, acl-model.md 0.3 "Creating items").

Every persona, every source they may cite, every destination of the fixtures, every action type for that platform: the
policy plane's decision is compared with a ground truth computed here, independently, from the fixture tokens. Pass bar:
**0 allowed writes whose destination has a reader who cannot read a source**, and a hidden destination is
indistinguishable from a missing one.
"""
from brain.policy import writes
from brain.policy.identity import IdentityResolver
from brain.policy.pdp import PDP
from brain.policy.writes import ProposedOp, WritePolicy
from connectors.stub.fixture_connector import FixtureConnector
from fixtures.loader import State, can_see

SOURCES = ("confluence", "jira", "slack", "gdrive")
TYPES = {platform: [t for t, p in writes.ACTION_TYPES.items() if p == platform] for platform in SOURCES}
FIELDS = {"summary": "s", "title": "t", "body": "b", "text": "x", "description": "d"}


def _world():
    state = State()
    connectors = {s: FixtureConnector(s, state) for s in SOURCES}
    resolver = IdentityResolver(connectors)
    return state, WritePolicy(connectors, resolver, PDP(connectors, policy_version="pol-test")), resolver


def _destinations(state):
    """Every container, and every document an operation can attach to (a thread to reply in, an issue to comment on)."""
    containers = {d["parent_id"] for d in state.docs.values()}
    return sorted(containers | set(state.docs))


def _readers(state, destination):
    """Ground truth: personas whose tokens overlap the ACL an item created there would get."""
    doc = state.docs.get(destination)
    tokens = set(doc["acl"]["tokens"]) if doc else set().union(
        *(d["acl"]["tokens"] for d in state.docs.values() if d["parent_id"] == destination))
    return [p for p in state.data["personas"] if state.persona_tokens[p["id"]] & tokens]


def test_no_allowed_write_reaches_a_reader_who_cannot_read_its_sources():
    state, policy, resolver = _world()
    labels = {d["doc_id"]: d["acl"]["tokens"] for d in state.docs.values()}
    allowed = blocked = 0
    for persona in state.data["personas"]:
        asker = resolver.resolve(persona["email"])
        cite = [d for d in state.docs.values() if can_see(state.persona_tokens[persona["id"]], d)]
        for destination in _destinations(state):
            platform = destination.split(":", 1)[0]
            readers = _readers(state, destination)
            for source in cite:
                for type_ in TYPES[platform]:
                    op = ProposedOp(0, type_, destination, FIELDS, (source["doc_id"],), links=(source["doc_id"],))
                    d = policy.decide(asker, [op], named=[destination], allowlist=[type_],
                                      source_tokens=labels).operations[0]
                    widening = [r["id"] for r in readers if not can_see(state.persona_tokens[r["id"]], source)]
                    if d.allowed:
                        allowed += 1
                        assert not widening, (persona["id"], source["doc_id"], destination, widening)
                        assert persona in readers                     # never into a place the person cannot see
                        assert d.links == (source["doc_id"],)         # its own source passed P4, so its link stays
                    else:
                        blocked += 1
                        if widening and d.reason == writes.AUDIENCE_WIDER:
                            assert d.blocking_sources == (source["doc_id"],)
                            assert d.blocked_readers == len(widening)
    assert allowed > 50 and blocked > 50                              # the matrix is not vacuous either way


def test_every_hidden_destination_answers_like_a_missing_one_for_every_persona():
    state, policy, resolver = _world()
    for persona in state.data["personas"]:
        asker = resolver.resolve(persona["email"])
        for destination in _destinations(state):
            if persona in _readers(state, destination):
                continue
            platform = destination.split(":", 1)[0]
            missing = f"{platform}:NO-SUCH-PLACE"
            for type_ in TYPES[platform]:
                hidden, absent = (policy.decide(asker, [ProposedOp(0, type_, dest, FIELDS)], named=[dest],
                                                allowlist=[type_], source_tokens={}).operations[0]
                                  for dest in (destination, missing))
                assert hidden == absent, (persona["id"], destination)
                assert hidden.reason == writes.NO_SUCH_DESTINATION


def test_a_destination_written_into_a_source_never_becomes_an_operation():
    """F6: content cannot add a destination. Only what the person named or picked is ever checked against the platform."""
    state, policy, resolver = _world()
    asker = resolver.resolve("priya@companya.com")
    injected = [ProposedOp(i, "slack.post_message", dest, {"text": "@channel all hands"}, ("slack:C_DBMIG/thread-1",))
                for i, dest in enumerate(("slack:C_AUTH", "slack:C_DBMIG", "slack:C_PAYINC"))]
    plan = policy.decide(asker, injected, named=["jira:DBMIG"], allowlist=list(writes.ACTION_TYPES),
                         source_tokens={d["doc_id"]: d["acl"]["tokens"] for d in state.docs.values()}, max_ops=25)
    assert plan.injection_flagged and not any(d.allowed for d in plan.operations)
    assert {d.reason for d in plan.operations} == {writes.NOT_NAMED}

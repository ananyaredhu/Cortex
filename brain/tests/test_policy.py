"""The policy plane: identity fails closed, the PDP denies with one shape, caches drop on outbox events."""
from brain.policy.events import OutboxConsumer
from brain.policy.identity import IdentityResolver
from brain.policy.labels import acl_label, may_serve
from brain.policy.leakscan import DeniedDoc, scan
from brain.policy.pdp import PDP, hash_denied
from connectors.base import AccessDecision, PlatformIdentity
from connectors.ingestion import FakeEmbedder, Ingestor, InMemoryStore
from connectors.stub.fixture_connector import FixtureConnector
from fixtures.loader import State

PRIYA = "priya@companya.com"
SAM = "sam@contractor.io"
BREACH = "confluence:SEC/q3-breach-report"


class Broken:
    source = "slack"

    def resolve_identity(self, email):
        raise ConnectionError("rate limited")

    def check_access(self, identity, doc_id):
        raise TimeoutError("slow")


class Liar:
    """A connector whose resolve works but whose check_access errors: the PDP must still deny."""
    source = "jira"

    def resolve_identity(self, email):
        return PlatformIdentity("jira", "u1", email, ["role:DBMIG:developer"])

    def check_access(self, identity, doc_id):
        raise RuntimeError("boom")


def _connectors(state=None):
    state = state or State()
    return {s: FixtureConnector(s, state) for s in ("confluence", "jira", "slack", "gdrive")}, state


def test_identity_is_user_token_plus_platform_groups_and_fails_closed_per_platform():
    connectors, _ = _connectors()
    connectors["slack"] = Broken()
    asker = IdentityResolver(connectors).resolve(PRIYA)
    assert f"user:{PRIYA}" in asker.tokens
    assert "role:DBMIG:developer" in asker.tokens
    assert not any(t.startswith("channel:") for t in asker.tokens)
    assert asker.unavailable == ("slack",) and "slack" not in asker.sources()


def test_unmapped_person_has_only_their_user_token():
    connectors, _ = _connectors()
    asker = IdentityResolver(connectors).resolve("nobody@example.com")
    assert asker.tokens == {"user:nobody@example.com"} and asker.sources() == ()


def test_pdp_denies_forbidden_missing_and_errors_with_one_shape():
    connectors, _ = _connectors()
    connectors["jira"] = Liar()
    resolver = IdentityResolver(connectors)
    pdp = PDP(connectors, policy_version="pol-test")
    sam = resolver.resolve(SAM)
    forbidden = pdp.check(sam, BREACH)
    missing = pdp.check(sam, "confluence:SEC/nope")
    errored = pdp.check(resolver.resolve(PRIYA), "jira:DBMIG-142")
    for d in (forbidden, missing, errored):
        assert not d.allowed and d.proof_path == ()
        view = d.audit_view("salt")
        assert "doc_id" not in view and view["doc_id_hash"].startswith("sha256:")
    assert errored.reason == "error"
    assert hash_denied(BREACH, "a") != hash_denied(BREACH, "b")


def test_pdp_allows_with_a_proof_path_and_records_evidence():
    connectors, _ = _connectors()
    resolver = IdentityResolver(connectors)
    pdp = PDP(connectors, policy_version="pol-test")
    d = pdp.check(resolver.resolve("dana@companya.com"), BREACH, "v1")
    assert d.allowed and d.proof_path[0] == "user:dana@companya.com" and d.acl_snapshot_hash
    view = d.audit_view("salt")
    assert view["doc_id"] == BREACH and view["jit_checked"] and view["doc_version"] == "v1"


def test_decision_cache_is_dropped_by_a_principal_change_from_the_outbox():
    connectors, state = _connectors()
    store = InMemoryStore()
    ingestor = Ingestor(connectors.values(), store, FakeEmbedder())
    ingestor.run_once()
    resolver = IdentityResolver(connectors, ttl_s=3600)
    pdp = PDP(connectors, policy_version="pol-test", ttl_s=3600)
    recorded: list[dict] = []
    consumer = OutboxConsumer(store, resolver, pdp, record=recorded.append)
    consumer.drain()
    thread = "slack:C_AUTHPRIV/thread-1"
    priya = resolver.resolve(PRIYA)
    assert pdp.check(priya, thread).allowed and "channel:C_AUTHPRIV" in priya.tokens

    for c in connectors.values():
        c.advance("e2")                                  # Priya loses the private channel
    ingestor.run_once()                                  # ingestion writes principal_change to the outbox
    # Without the consumer, the long-lived caches would still say yes.
    assert pdp.check(priya, thread).allowed
    assert consumer.drain() == 1 and recorded[-1]["event_type"] == "acl_change_observed"
    priya = resolver.resolve(PRIYA)
    assert "channel:C_AUTHPRIV" not in priya.tokens
    assert not pdp.check(priya, thread).allowed


def test_pdp_checks_connector_answer_against_fake_identity():
    """A connector decision is live: swapping the identity's groups does not change the connector's answer."""
    connectors, _ = _connectors()
    pdp = PDP(connectors, policy_version="pol-test")
    asker = IdentityResolver(connectors).resolve(SAM)
    forged = PlatformIdentity("confluence", "x", SAM, ["group:confluence:security-team"])
    assert not connectors["confluence"].check_access(forged, BREACH).allowed
    assert not pdp.check(asker, BREACH).allowed


def test_labels_are_a_conjunction_over_sources():
    label = acl_label([["role:DBMIG:developer", "role:DBMIG:lead"], ["channel:C_DBMIG", "public:org"]])
    assert may_serve({"role:DBMIG:developer", "public:org"}, label)
    assert not may_serve({"role:DBMIG:developer"}, label)
    assert may_serve({"anything"}, acl_label([]))


def test_leak_scan_catches_ids_titles_canaries_and_phrases():
    denied = [DeniedDoc(BREACH, "Q3 security incident report: breach",
                        "Root cause: an admin token was replayed. Canary CANARY-SEC-7781 marks this document. "
                        "The attacker used the replayed admin token to read customer records for six hours before detection.")]
    assert scan("nothing related here", denied) == []
    assert scan("see confluence:SEC/q3-breach-report", denied) == [BREACH]
    assert scan("The Q3 security incident report: breach says so", denied) == [BREACH]
    assert scan("code CANARY-SEC-7781", denied) == [BREACH]
    assert scan("the attacker used the replayed admin token to read customer records for six hours", denied) == [BREACH]


def test_access_decision_never_leaks_into_the_decision_shape():
    """The connector's AccessDecision stays in the policy plane: Decision carries only what the audit log needs."""
    d = AccessDecision(True, ["user:x"], "t", "h", "p")
    assert not hasattr(d, "audit_view")

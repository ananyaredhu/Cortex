"""The write policy (acl-model.md 0.3, "Creating items"): checks P1 to P6, the audience rule, references to created items.

The rows of the worked example in acl-model.md are tests here. Every check fails closed.
"""
import json

from brain.policy import writes
from brain.policy.identity import IdentityResolver
from brain.policy.pdp import PDP
from brain.policy.writes import ProposedOp, WritePolicy, resolve_person
from connectors.base import AccessDecision, Audience
from connectors.stub.fixture_connector import FixtureConnector
from fixtures.loader import State

PRIYA, SAM, DANA, JORDAN, MAYA = (f"{p}@companya.com" for p in ("priya", "sam", "dana", "jordan", "maya"))
SAM = "sam@contractor.io"
SOURCES = ("confluence", "jira", "slack", "gdrive")
ALL = tuple(writes.ACTION_TYPES)
DBMIG_THREAD = "slack:C_DBMIG/thread-1"          # public:org + channel:C_DBMIG
AUTH_PRIVATE = "slack:C_AUTHPRIV/thread-1"       # channel:C_AUTHPRIV: Priya only
DBMIG_142 = "jira:DBMIG-142"                      # DBMIG roles: Priya, Maya
SEC_17 = "jira:SEC-17"                            # security team: Dana only
RUNBOOK = "confluence:PAY/runbook-payment-service"   # public:org
LABELS = {d["doc_id"]: d["acl"]["tokens"] for d in State().docs.values()}


def _setup(state=None, **overrides):
    state = state or State()
    connectors = {s: FixtureConnector(s, state) for s in SOURCES}
    connectors.update(overrides)
    resolver = IdentityResolver(connectors)
    pdp = PDP(connectors, policy_version="pol-test")
    return WritePolicy(connectors, resolver, pdp), resolver, state


def _op(index, type_, destination, cites=(), **kw):
    fields = kw.pop("fields", {"text": "Drafted from the sources."})
    return ProposedOp(index, type_, destination, fields, tuple(cites), **kw)


def _decide(who, *ops, setup=None, named=None, allow=ALL, max_ops=25, mentions=False, labels=LABELS):
    policy, resolver, _ = setup or _setup()
    named = [o.destination for o in ops] if named is None else named
    return policy.decide(resolver.resolve(who), ops, named=named, allowlist=allow, source_tokens=labels,
                         max_ops=max_ops, mentions_allowed=mentions)


def _one(who, op, **kw):
    return _decide(who, op, **kw).operations[0]


# -- the worked example in acl-model.md ------------------------------------------------------------------------------
def test_a_public_thread_into_the_project_whose_members_can_all_read_it_is_allowed():
    d = _one(PRIYA, _op(0, "jira.create_issue", "jira:DBMIG", [DBMIG_THREAD], fields={"summary": "Blocker"}))
    assert d.allowed and d.reason is None
    assert d.checks == {c: "pass" for c in writes.CHECKS}
    assert d.audience_snapshot_hash.startswith("sha256:")


def test_a_private_thread_into_a_public_channel_is_blocked_and_names_only_the_persons_own_source():
    d = _one(PRIYA, _op(0, "slack.post_message", "slack:C_AUTH", [AUTH_PRIVATE]))
    assert not d.allowed and d.reason == writes.AUDIENCE_WIDER and d.checks["P4"] == "fail"
    assert d.blocking_sources == (AUTH_PRIVATE,)
    assert d.blocked_readers == 3                          # Dana, Jordan and Maya: a count, never who
    view = json.dumps(d.public_view())
    assert "@" not in view and not any(name in view for name in ("dana", "jordan", "maya"))
    assert d.fields == {} and d.links == ()                # nothing of a blocked operation is passed on


def test_the_same_thread_back_into_its_own_private_channel_is_allowed():
    assert _one(PRIYA, _op(0, "slack.post_message", "slack:C_AUTHPRIV", [AUTH_PRIVATE])).allowed


def test_a_project_issue_into_a_public_channel_is_blocked_because_the_whole_org_reads_it():
    d = _one(PRIYA, _op(0, "slack.post_message", "slack:C_DBMIG", [DBMIG_142]))
    assert d.reason == writes.AUDIENCE_WIDER and d.blocking_sources == (DBMIG_142,) and d.blocked_readers == 2


def test_a_hidden_destination_and_a_missing_one_give_the_same_decision():
    hidden = _one(SAM, _op(0, "confluence.create_page", "confluence:SEC", fields={"title": "x", "body": "y"}))
    missing = _one(SAM, _op(0, "confluence.create_page", "confluence:XYZ", fields={"title": "x", "body": "y"}))
    assert hidden == missing
    assert hidden.reason == writes.NO_SUCH_DESTINATION and hidden.audience_snapshot_hash == ""


class Unlisted:
    """A Drive whose folder is shared by link: its readers cannot be listed."""
    source = "gdrive"

    def __init__(self, inner, external):
        self.inner, self.external = inner, external

    def resolve_identity(self, email):
        return self.inner.resolve_identity(email)

    def check_access(self, identity, doc_id):
        return self.inner.check_access(identity, doc_id)

    def can_create(self, identity, destination):
        return True

    def audience(self, destination):
        return Audience(None, self.external, "sha256:link")


def test_an_audience_that_cannot_be_listed_takes_only_org_wide_content_and_no_external_reader():
    op = _op(0, "gdrive.create_doc", "gdrive:folder-link", [RUNBOOK], fields={"title": "t", "body": "b"})
    state = State()
    inside = _setup(state, gdrive=Unlisted(FixtureConnector("gdrive", state), external=False))
    outside = _setup(State(), gdrive=Unlisted(FixtureConnector("gdrive", State()), external=True))
    assert _one(PRIYA, op, setup=inside).allowed
    d = _one(PRIYA, op, setup=outside)
    assert d.reason == writes.AUDIENCE_NOT_ENUMERABLE and d.checks["P5"] == "fail"
    narrow = _one(PRIYA, _op(0, "gdrive.create_doc", "gdrive:folder-link", [DBMIG_142]), setup=_setup(
        State(), gdrive=Unlisted(FixtureConnector("gdrive", State()), external=False)))
    assert narrow.reason == writes.AUDIENCE_NOT_ENUMERABLE and narrow.blocking_sources == (DBMIG_142,)


# -- references to created items (ADR-010, Q7) -----------------------------------------------------------------------
def test_a_reply_listing_new_cards_is_blocked_where_the_cards_readers_are_narrower_and_the_cards_still_go_ahead():
    cards = _op(0, "jira.create_issue", "jira:DBMIG", [DBMIG_THREAD], fields={"summary": "Blocker"})
    reply = _op(1, "slack.post_message", "slack:C_DBMIG", [DBMIG_THREAD], refs=(0,))
    plan = _decide(MAYA, cards, reply)
    assert plan.operations[0].allowed
    assert plan.operations[1].reason == writes.AUDIENCE_WIDER and plan.operations[1].blocking_refs == (0,)
    assert plan.operations[1].blocking_sources == ()


def test_the_same_reply_in_a_channel_whose_members_all_read_the_project_is_allowed():
    cards = _op(0, "jira.create_issue", "jira:DBMIG", [DBMIG_THREAD], fields={"summary": "Blocker"})
    reply = _op(1, "slack.post_message", "slack:C_DBMIGPRIV", refs=(0,))
    assert all(d.allowed for d in _decide(MAYA, cards, reply).operations)


def test_a_reference_to_a_blocked_or_a_later_operation_is_not_allowed():
    blocked = _op(0, "slack.post_message", "slack:C_AUTH", [AUTH_PRIVATE])
    ref = _op(1, "slack.post_message", "slack:C_AUTHPRIV", refs=(0,))
    forward = _op(2, "slack.post_message", "slack:C_AUTHPRIV", refs=(3,))
    plan = _decide(PRIYA, blocked, ref, forward)
    assert plan.operations[1].reason == writes.REFERENCE_BLOCKED and plan.operations[1].blocking_refs == (0,)
    assert plan.operations[2].reason == writes.BAD_REFERENCE


# -- before the checks: destination from the person, allowlist, cap -------------------------------------------------
def test_a_destination_the_person_did_not_name_is_rejected_and_flagged_as_injection():
    plan = _decide(PRIYA, _op(0, "slack.post_message", "slack:C_AUTHPRIV", [AUTH_PRIVATE]),
                   _op(1, "slack.post_message", "slack:C_AUTH", [AUTH_PRIVATE]), named=["slack:C_AUTHPRIV"])
    assert plan.operations[0].allowed
    assert plan.operations[1].reason == writes.NOT_NAMED and plan.operations[1].injection_flagged
    assert plan.injection_flagged


def test_only_allowlisted_action_types_on_their_own_platform_are_allowed():
    assert _one(PRIYA, _op(0, "slack.post_message", "slack:C_AUTHPRIV"), allow=["jira.create_issue"]).reason \
        == writes.NOT_ALLOWED_TYPE
    assert _one(PRIYA, _op(0, "slack.post_message", "jira:DBMIG")).reason == writes.NOT_ALLOWED_TYPE
    assert _one(PRIYA, _op(0, "jira.transition", "jira:DBMIG"), allow=["jira.transition"]).reason \
        == writes.NOT_ALLOWED_TYPE


def test_a_plan_over_the_cap_is_cut_with_a_visible_note():
    ops = [_op(i, "jira.create_issue", "jira:DBMIG", [DBMIG_THREAD], fields={"summary": f"c{i}"}) for i in range(30)]
    plan = _decide(PRIYA, *ops, max_ops=99)
    assert len(plan.operations) == writes.MAX_OPS and plan.notes
    assert len(_decide(PRIYA, *ops[:3], max_ops=1).operations) == 1


# -- P1, P3 ----------------------------------------------------------------------------------------------------------
def test_a_cited_source_the_person_cannot_open_blocks_without_being_named():
    for cited in (SEC_17, "jira:SEC-999"):
        d = _one(PRIYA, _op(0, "jira.create_issue", "jira:DBMIG", [cited]))
        assert d.reason == writes.SOURCES_CHANGED and d.checks["P1"] == "fail"
        assert "SEC" not in json.dumps(d.public_view())


def test_a_source_revoked_after_planning_fails_the_next_check():
    setup = _setup()
    op = _op(0, "slack.post_message", "slack:C_AUTHPRIV", [AUTH_PRIVATE])
    assert _one(PRIYA, op, setup=setup).allowed
    policy, resolver, state = setup
    state.advance("e2")                                     # Priya leaves the private auth channel
    resolver.invalidate(PRIYA)
    policy._pdp.invalidate_user(PRIYA)
    assert not _one(PRIYA, op, setup=setup).allowed


def test_reading_a_channel_is_not_permission_to_post_in_it():
    d = _one(JORDAN, _op(0, "slack.post_message", "slack:C_DBMIG", typed_fields=frozenset({"text"})))
    assert d.reason == writes.NO_WRITE_PERMISSION and d.checks["P2"] == "pass" and d.checks["P3"] == "fail"


def test_text_the_person_typed_carries_no_label():
    d = _one(PRIYA, _op(0, "slack.post_message", "slack:C_DBMIG", fields={"text": "Standup moved to 10:30"},
                        typed_fields=frozenset({"text"})))
    assert d.allowed and d.fields == {"text": "Standup moved to 10:30"}


# -- P6 --------------------------------------------------------------------------------------------------------------
def test_content_limits_are_fixed_and_reported():
    drafted = "@channel see https://evil.example/x and <!here> now " + "x" * 5000
    d = _one(PRIYA, _op(0, "slack.post_message", "slack:C_DBMIG", [DBMIG_THREAD], fields={"text": drafted},
                        links=(DBMIG_THREAD, DBMIG_142)))
    assert d.allowed and d.checks["P6"] == "fixed"
    text = d.fields["text"]
    assert "@channel" not in text and "<!here>" not in text and "evil.example" not in text and len(text) == 4000
    changes = {(a["field"], a["change"]) for a in d.autofixes}
    assert {("text", "link_removed"), ("text", "mention_removed"), ("text", "truncated"), ("links", "link_removed")} \
        <= changes
    assert d.links == (DBMIG_THREAD,)                      # DBMIG-142 is not readable by the whole channel


def test_mentions_a_skill_allows_need_their_own_confirm_and_typed_links_stay():
    d = _one(PRIYA, _op(0, "slack.post_message", "slack:C_AUTHPRIV", fields={"text": "@here deploy at 5\nsee https://x.io"},
                        typed_fields=frozenset({"text"})), mentions=True)
    assert d.allowed and d.fields["text"] == "@here deploy at 5\nsee https://x.io"
    assert d.confirm_lines == ("@here deploy at 5",)


# -- fail closed -----------------------------------------------------------------------------------------------------
class Failing(FixtureConnector):
    def __init__(self, source, state, *, audience=False, create=False, deny=None):
        super().__init__(source, state)
        self.fail_audience, self.fail_create, self.deny = audience, create, deny or set()

    def audience(self, destination):
        if self.fail_audience:
            raise TimeoutError("slow")
        return super().audience(destination)

    def can_create(self, identity, destination):
        if self.fail_create:
            raise ConnectionError("down")
        return super().can_create(identity, destination)

    def check_access(self, identity, doc_id):
        if (identity.email, doc_id) in self.deny:
            return AccessDecision(False, [], "", "h", "x")
        return super().check_access(identity, doc_id)


def test_errors_and_live_denials_all_block():
    op = _op(0, "jira.create_issue", "jira:DBMIG", [DBMIG_142])
    s1 = State()
    assert _one(PRIYA, op, setup=_setup(s1, jira=Failing("jira", s1, audience=True))).reason == writes.NO_SUCH_DESTINATION
    s2 = State()
    assert _one(PRIYA, op, setup=_setup(s2, jira=Failing("jira", s2, create=True))).reason == writes.NO_WRITE_PERMISSION
    # Maya's tokens satisfy DBMIG-142's label, but the platform says no: the live check wins (the narrowing rule)
    s3 = State()
    d = _one(PRIYA, op, setup=_setup(s3, jira=Failing("jira", s3, deny={(MAYA, DBMIG_142)})))
    assert d.reason == writes.AUDIENCE_WIDER and d.blocked_readers == 1


def test_a_source_without_a_known_label_is_never_cleared():
    labels = {k: v for k, v in LABELS.items() if k != DBMIG_THREAD}
    d = _one(PRIYA, _op(0, "jira.create_issue", "jira:DBMIG", [DBMIG_THREAD]), labels=labels)
    assert d.reason == writes.SOURCES_CHANGED


def test_an_audience_too_large_to_check_is_treated_as_not_enumerable():
    policy, resolver, _ = _setup()
    small = WritePolicy(policy._connectors, resolver, policy._pdp, max_audience=1)
    def decide(cited):
        return small.decide(resolver.resolve(PRIYA), [_op(0, "jira.create_issue", "jira:DBMIG", [cited])],
                            named=["jira:DBMIG"], allowlist=ALL, source_tokens=LABELS).operations[0]
    assert decide(DBMIG_142).reason == writes.AUDIENCE_NOT_ENUMERABLE
    assert decide(DBMIG_THREAD).allowed                    # org-wide content, nobody external: P5's one exception


# -- people fields (F9) ----------------------------------------------------------------------------------------------
def test_a_display_name_resolves_to_one_account_or_none():
    directory = {"Priya": [PRIYA], "Sam": ["sam@a.io", "sam@b.io"]}
    assert resolve_person("  priya ", directory) == PRIYA
    assert resolve_person("Sam", directory) is None and resolve_person("Nobody", directory) is None
    assert resolve_person("", directory) is None

"""The Slack connector against the in-memory fake, in the workspace's own IDs.

The shared contract tests (connectors/tests/contract) cover the seeded story. These cover what is specific to
Slack: guests, the bot's own membership, public/private flips, threads, paging, rate limits and failure.
"""
import time

import pytest

from connectors.base import DocumentNotFound, PlatformIdentity
from connectors.ingestion import FakeEmbedder, Ingestor, InMemoryStore
from connectors.slack import SlackConnector, SlackError
from connectors.slack.fake import FakeSlack, seed_company_a
from fixtures.loader import load

DATA = load()


class World:
    def __init__(self, **client_kwargs):
        self.slack = FakeSlack()
        self.identities, self.manifest = seed_company_a(self.slack, DATA)
        self.conn = SlackConnector(self.slack.client(**client_kwargs), self.identities)

    def channel(self, fixture_id: str) -> str:
        return self.manifest.channels[fixture_id]

    def doc(self, fixture_doc_id: str) -> str:
        return self.manifest.real_doc(fixture_doc_id)

    def user(self, persona: str) -> str:
        return self.slack.user_id(f"{persona}.account@example.com")

    def identity(self, persona: str) -> PlatformIdentity:
        email = next(p["email"] for p in DATA["personas"] if p["id"] == persona)
        return self.conn.resolve_identity(email)

    def allowed(self, persona: str, doc_id: str) -> bool:
        return self.conn.check_access(self.identity(persona), doc_id).allowed

    def changes(self, cursor: str):
        batch = self.conn.list_changes(cursor)
        return [(c.type, c.doc_id, c.principal, c.token) for c in batch.changes], batch.next_cursor


@pytest.fixture
def w() -> World:
    return World()


# -- documents ----------------------------------------------------------------------------------
def test_document_shape(w):
    doc_id = w.doc("slack:C_DBMIG/thread-1")
    doc = w.conn.fetch(doc_id)
    channel = w.channel("C_DBMIG")
    ts = w.manifest.threads["slack:C_DBMIG/thread-1"]
    assert (doc.doc_id, doc.kind, doc.parent_id, doc.version) == (doc_id, "thread", f"slack:{channel}", ts)
    assert doc.title.startswith("#db-migration thread: Blockers raised last week")
    assert doc.url == f"https://companya.slack.test/archives/{channel}/p{ts.replace('.', '')}"
    assert doc.author == "priya@companya.com" and doc.created_at == doc.updated_at == "2026-10-10T08:00:00Z"
    assert doc.acl.tokens == [f"channel:{channel}", "public:org"]
    assert doc.acl.native == {"channel": channel, "private": False} and doc.acl.snapshot_hash.startswith("sha256:")


def test_author_is_none_when_the_account_is_not_mapped(w):
    assert w.conn.fetch(w.doc("slack:C_DBMIG/thread-2")).author is None


def test_replies_join_the_thread_and_change_its_version(w):
    doc_id, channel = w.doc("slack:C_AUTH/thread-1"), w.channel("C_AUTH")
    root_ts = doc_id.split("/")[1]
    _, cursor = w.changes(None)
    reply_ts = w.slack.post(channel, w.user("dana"), "Agreed on short lifetimes.", thread_ts=root_ts)
    assert w.changes(cursor)[0] == [("upsert", doc_id, None, None)]
    doc = w.conn.fetch(doc_id)
    assert doc.body.endswith("\n\nAgreed on short lifetimes.") and doc.version == w.conn.version(doc_id) == reply_ts
    with pytest.raises(DocumentNotFound):
        w.conn.fetch(f"slack:{channel}/{reply_ts}")   # a reply is part of its thread, not a document


def test_editing_and_deleting_a_thread(w):
    doc_id, channel = w.doc("slack:C_PAYINC/thread-1"), w.channel("C_PAYINC")
    ts = doc_id.split("/")[1]
    _, cursor = w.changes(None)
    w.slack.edit(channel, ts, "Corrected timeline.")
    changes, cursor = w.changes(cursor)
    assert changes == [("upsert", doc_id, None, None)]
    assert w.conn.fetch(doc_id).body == "Corrected timeline." and float(w.conn.version(doc_id)) > float(ts)
    w.slack.delete(channel, ts)
    assert w.changes(cursor)[0] == [("delete", doc_id, None, None)]
    with pytest.raises(DocumentNotFound):
        w.conn.version(doc_id)
    decision = w.conn.check_access(w.identity("maya"), doc_id)
    assert (decision.allowed, decision.proof_path, decision.acl_snapshot_hash) == (False, [], "")


def test_joins_and_other_system_messages_are_not_documents(w):
    channel = w.channel("C_DBMIG")
    _, cursor = w.changes(None)
    ts = w.slack.post(channel, w.user("dana"), "<@dana> has joined the channel", subtype="channel_join")
    assert w.changes(cursor)[0] == []
    with pytest.raises(DocumentNotFound):
        w.conn.fetch(f"slack:{channel}/{ts}")
    assert not w.allowed("dana", f"slack:{channel}/{ts}")


# -- who may read -------------------------------------------------------------------------------
def test_guest_reads_only_the_channels_they_are_in(w):
    sam = w.identity("sam")
    assert sam.groups == [f"channel:{w.channel('C_AUTH')}"], "a guest never holds public:org"
    decision = w.conn.check_access(sam, w.doc("slack:C_AUTH/thread-1"))
    assert decision.allowed and decision.proof_path == ["user:sam@contractor.io", f"channel:{w.channel('C_AUTH')}"]
    assert not w.allowed("sam", w.doc("slack:C_DBMIG/thread-1")), "public, but Sam is a guest and not a member"


def test_full_member_reads_any_public_channel_without_joining(w):
    decision = w.conn.check_access(w.identity("jordan"), w.doc("slack:C_PAYINC/thread-1"))
    assert decision.allowed and decision.proof_path == ["user:jordan@companya.com", "public:org"]
    assert not w.allowed("jordan", w.doc("slack:C_AUTHPRIV/thread-1"))


def test_channel_going_private_is_an_acl_change_on_its_threads(w):
    channel = w.channel("C_DBMIG")
    docs = sorted(w.doc(f"slack:C_DBMIG/thread-{n}") for n in (1, 2))
    before = w.conn.fetch(docs[0]).acl
    assert w.allowed("jordan", docs[0])
    _, cursor = w.changes(None)
    w.slack.channels[channel]["private"] = True
    assert w.changes(cursor)[0] == [("acl_change", d, None, None) for d in docs]
    after = w.conn.fetch(docs[0]).acl
    assert after.tokens == [f"channel:{channel}"] and after.snapshot_hash != before.snapshot_hash
    assert not w.allowed("jordan", docs[0]) and w.allowed("priya", docs[0])


def test_membership_changes_are_principal_changes(w):
    channel = w.channel("C_DBMIGPRIV")
    doc_id = w.doc("slack:C_DBMIGPRIV/thread-1")
    _, cursor = w.changes(None)
    assert not w.allowed("dana", doc_id)
    w.slack.join(channel, w.user("dana"))
    changes, cursor = w.changes(cursor)
    assert changes == [("principal_change", None, "user:dana@companya.com", f"channel:{channel}")]
    assert w.allowed("dana", doc_id) and f"channel:{channel}" in w.identity("dana").groups
    w.slack.users[w.user("sam")].update(is_restricted=False, is_ultra_restricted=False)   # the guest becomes a full member
    assert w.changes(cursor)[0] == [("principal_change", None, "user:sam@contractor.io", "public:org")]


def test_deactivated_or_unmapped_accounts_have_no_access(w):
    doc_id = w.doc("slack:C_DBMIG/thread-1")
    dana = w.identity("dana")
    w.slack.users[w.user("dana")]["deleted"] = True
    assert w.conn.resolve_identity("dana@companya.com") is None
    assert not w.conn.check_access(dana, doc_id).allowed, "a stale identity object does not keep access"
    stranger = PlatformIdentity("slack", w.slack.user_id("someone.unmapped@example.com"), "priya@companya.com", ["public:org"])
    assert not w.conn.check_access(stranger, doc_id).allowed, "the account must map to the email the identity claims"
    assert not w.conn.check_access(PlatformIdentity("jira", w.user("priya"), "priya@companya.com", []), doc_id).allowed


def test_a_channel_without_the_bot_does_not_exist(w):
    doc_id, channel = w.doc("slack:C_AUTHPRIV/thread-1"), w.channel("C_AUTHPRIV")
    priya = w.identity("priya")
    _, cursor = w.changes(None)
    w.slack.channels[channel]["bot"] = False
    changes, _ = w.changes(cursor)
    assert changes == [("delete", doc_id, None, None)], "its documents go; nobody's membership is reported as changed"
    with pytest.raises(DocumentNotFound):
        w.conn.fetch(doc_id)
    assert not w.conn.check_access(priya, doc_id).allowed


# -- the change feed ----------------------------------------------------------------------------
def test_crawl_is_stable_and_a_foreign_cursor_resyncs(w):
    crawl, cursor = w.changes(None)
    assert [c[0] for c in crawl] == ["upsert"] * 6 and sorted(c[1] for c in crawl) == sorted(
        w.doc(d["doc_id"]) for d in DATA["documents"] if d["source"] == "slack")
    assert w.changes(cursor) == ([], cursor)
    restarted = SlackConnector(w.slack.client(), w.identities)
    again = restarted.list_changes(cursor)   # a cursor saved by the previous process
    assert sorted(c.doc_id for c in again.changes) == sorted(c[1] for c in crawl) and again.next_cursor != cursor
    assert restarted.list_changes(again.next_cursor).changes == []
    assert restarted.list_changes("garbage").changes != []


def test_paging_is_followed_everywhere(w):
    w.slack.page_size = 1
    assert len(w.changes(None)[0]) == 6
    assert w.allowed("sam", w.doc("slack:C_AUTH/thread-1")), "the member is not on the first page of members"
    assert len(w.identity("priya").groups) == 5
    w.slack.post(w.channel("C_AUTH"), w.user("dana"), "A reply.", thread_ts=w.doc("slack:C_AUTH/thread-1").split("/")[1])
    assert w.conn.fetch(w.doc("slack:C_AUTH/thread-1")).body.endswith("A reply.")


# -- rate limits and failure --------------------------------------------------------------------
def test_ingestion_side_backs_off_on_429_and_the_query_side_fails_closed():
    waits: list[float] = []
    w = World(sleep=waits.append)
    w.slack.retry_after = "7"
    w.slack.throttle = 2
    assert len(w.changes(None)[0]) == 6 and waits == [7.0, 7.0]
    priya = w.identity("priya")
    w.slack.throttle = 1
    assert not w.conn.check_access(priya, w.doc("slack:C_DBMIG/thread-1")).allowed, "no waiting on the query path: deny"
    w.slack.throttle = 1
    assert w.conn.resolve_identity("priya@companya.com") is None
    assert waits == [7.0, 7.0]
    w.slack.throttle = 10
    with pytest.raises(SlackError, match="ratelimited"):
        w.conn.list_changes(None)


def test_everything_fails_closed_when_slack_is_unreachable(w):
    priya = w.identity("priya")
    doc_id = w.doc("slack:C_DBMIG/thread-1")
    w.slack.down = True
    assert w.conn.resolve_identity("priya@companya.com") is None
    decision = w.conn.check_access(priya, doc_id)
    assert (decision.allowed, decision.proof_path) == (False, [])
    with pytest.raises(Exception):   # noqa: B017  ingestion must see the failure, not an empty feed
        w.conn.list_changes(None)


def test_access_check_asks_slack_in_parallel_and_denies_when_out_of_time(w):
    doc_id = w.doc("slack:C_AUTH/thread-1")
    sam, priya = w.identity("sam"), w.identity("priya")
    w.slack.delay = 0.2
    started = time.perf_counter()
    assert w.conn.check_access(sam, doc_id).allowed, "a guest: all four questions are needed"
    assert time.perf_counter() - started < 0.6, "four 0.2 s calls one after another would take 0.8 s"
    hurried = SlackConnector(w.slack.client(), w.identities, access_timeout=0.05)
    decision = hurried.check_access(priya, doc_id)
    assert (decision.allowed, decision.proof_path, decision.acl_snapshot_hash) == (False, [], "")


# -- into the index -----------------------------------------------------------------------------
def test_ingestion_indexes_slack_and_follows_its_changes(w):
    store = InMemoryStore()
    ingestor = Ingestor([w.conn], store, FakeEmbedder())
    ingestor.run_once()
    doc_id, channel = w.doc("slack:C_AUTHPRIV/thread-1"), w.channel("C_AUTHPRIV")
    assert len(store.live_doc_ids("slack")) == 6
    assert store.chunks_of(doc_id)[0].acl_tokens == [f"channel:{channel}"]

    w.slack.leave(channel, w.user("priya"))                              # scripted event e2, done by hand in Slack
    public = w.channel("C_PAYINC")
    w.slack.channels[public]["private"] = True
    report = ingestor.run_once()
    assert dict(report.actions["slack"]) == {"acl_rewritten": 1, "principal_change": 1}
    assert [(e.kind, e.principal, e.token, e.doc_id) for e in store.events_after(0)] == [
        ("acl_change", None, None, w.doc("slack:C_PAYINC/thread-1")),
        ("principal_change", "user:priya@companya.com", f"channel:{channel}", None)]
    assert store.chunks_of(w.doc("slack:C_PAYINC/thread-1"))[0].acl_tokens == [f"channel:{public}"]

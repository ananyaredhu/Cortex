"""Slack DMs and group DMs against the in-memory fake (docs/02-contracts/acl-model.md, "Slack DMs").

The rules under test: a DM is a document only while everyone in it has authorized; it carries `user:` tokens
only; `check_access` asks with the asker's own token; a withdrawn authorization deletes the DMs; the bot's
token never reads one.
"""
import json

import pytest

from connectors.base import DocumentNotFound
from connectors.ingestion import FakeEmbedder, Ingestor, InMemoryStore
from connectors.slack import SlackConnector
from connectors.slack.authorize import code_from, consent_url, exchange
from connectors.slack.fake import USER_TOKEN_PREFIX
from connectors.slack.tests.test_slack_connector import EMAILS, World
from connectors.slack.user_tokens import UserGrant, UserTokens, token_path

SECRET = "The acquisition code name is Bluefin."


class DmWorld(World):
    """Company A, where Priya, Dana and Maya have authorized DMs. Jordan has an account and has not."""

    def __init__(self, authorized: tuple[str, ...] = ("priya", "dana", "maya")) -> None:
        super().__init__()
        self.tokens = UserTokens({EMAILS[p]: self.grant(p) for p in authorized})
        self.conn = SlackConnector(self.slack.client(), self.identities, user_tokens=self.tokens)

    def grant(self, persona: str, *, token_of: str | None = None) -> UserGrant:
        return UserGrant(EMAILS[persona], self.user(persona), self.slack.client(as_user=self.user(token_of or persona)))

    def dm(self, *personas: str, text: str = SECRET) -> str:
        """Start a DM between these personas with one message from the first. Returns the doc_id."""
        channel = self.slack.add_dm(*(self.user(p) for p in personas))
        return f"slack:{channel}/{self.slack.post(channel, self.user(personas[0]), text)}"

    def crawl(self) -> set[str]:
        return {c.doc_id for c in self.conn.list_changes(None).changes}


@pytest.fixture
def w() -> DmWorld:
    return DmWorld()


def _denied_like_nothing(w: DmWorld, persona: str, doc_id: str) -> bool:
    """Denied, with exactly the answer a DM that does not exist gets."""
    decision, nothing = (w.conn.check_access(w.identity(persona), d) for d in (doc_id, "slack:D0NOSUCHDM/1791619200.000100"))
    return (decision.allowed, decision.proof_path, decision.acl_snapshot_hash) == (False, [], "") \
        == (nothing.allowed, nothing.proof_path, nothing.acl_snapshot_hash)


# -- a DM as a document ---------------------------------------------------------------------------
def test_a_dm_is_a_document_with_one_user_token_per_participant(w):
    doc_id = w.dm("priya", "dana")
    channel = doc_id.split(":")[1].split("/")[0]
    assert doc_id in w.crawl()
    doc = w.conn.fetch(doc_id)
    assert (doc.kind, doc.parent_id, doc.body, doc.author) == ("thread", f"slack:{channel}", SECRET, EMAILS["priya"])
    assert doc.title == "DM dana, priya thread: The acquisition code name is Bluefin."
    assert doc.acl.tokens == ["user:dana@companya.com", "user:priya@companya.com"]
    assert doc.acl.native == {"channel": channel, "dm": True, "participants": [EMAILS["dana"], EMAILS["priya"]]}
    assert w.conn.version(doc_id) == doc.version == doc_id.split("/")[1]


def test_only_the_participants_pass_the_live_check(w):
    doc_id = w.dm("priya", "dana")
    for persona in ("priya", "dana"):
        decision = w.conn.check_access(w.identity(persona), doc_id)
        assert decision.allowed and decision.proof_path == [f"user:{EMAILS[persona]}"]
        assert decision.acl_snapshot_hash == w.conn.fetch(doc_id).acl.snapshot_hash
    assert _denied_like_nothing(w, "maya", doc_id)       # authorized, but not in this DM
    assert _denied_like_nothing(w, "jordan", doc_id)     # has not authorized at all
    assert not w.conn.check_access(w.identity("priya"), doc_id.split("/")[0] + "/1791619200.999999").allowed   # no such thread


def test_no_membership_or_group_gives_a_dm(w):
    """Nothing a person holds on Slack overlaps a DM's tokens: only the `user:` token the PDP adds for them."""
    doc = w.conn.fetch(w.dm("priya", "dana"))
    for persona in ("priya", "dana", "maya", "jordan"):
        assert not set(w.identity(persona).groups) & set(doc.acl.tokens)


def test_a_dm_needs_everyone_in_it_to_have_authorized(w):
    with_jordan = w.dm("priya", "jordan")                                        # Jordan has not authorized
    outsider = w.slack.user_id("someone.unmapped@example.com")
    channel = w.slack.add_dm(w.user("priya"), outsider)                          # not a mapped person
    with_outsider = f"slack:{channel}/{w.slack.post(channel, outsider, SECRET)}"
    crawled = w.crawl()
    for doc_id in (with_jordan, with_outsider):
        assert doc_id not in crawled
        with pytest.raises(DocumentNotFound):
            w.conn.fetch(doc_id)
        with pytest.raises(DocumentNotFound):
            w.conn.version(doc_id)
        assert _denied_like_nothing(w, "priya", doc_id)                          # even for the one who did agree


def test_a_note_to_self_is_a_document_for_that_person_alone(w):
    doc_id = w.dm("priya")
    assert doc_id in w.crawl() and w.conn.fetch(doc_id).acl.tokens == ["user:priya@companya.com"]
    assert w.allowed("priya", doc_id) and not w.allowed("dana", doc_id)


def test_a_group_dm_follows_the_same_rules(w):
    doc_id = w.dm("priya", "dana", "maya")
    assert doc_id.split(":")[1].startswith("G")                                  # a channel-like ID the bot cannot see
    not_all = w.dm("priya", "dana", "jordan")
    crawled = w.crawl()
    assert doc_id in crawled and not_all not in crawled
    assert w.conn.fetch(doc_id).acl.tokens == [f"user:{EMAILS[p]}" for p in ("dana", "maya", "priya")]
    assert all(w.allowed(p, doc_id) for p in ("priya", "dana", "maya"))
    assert _denied_like_nothing(w, "jordan", doc_id) and _denied_like_nothing(w, "priya", not_all)


def test_the_bot_token_alone_reads_no_dm(w):
    doc_id = w.dm("priya", "dana")
    bot_only = SlackConnector(w.slack.client(), w.identities)
    assert doc_id not in {c.doc_id for c in bot_only.list_changes(None).changes}
    with pytest.raises(DocumentNotFound):
        bot_only.fetch(doc_id)
    assert not bot_only.check_access(bot_only.resolve_identity(EMAILS["priya"]), doc_id).allowed


def test_a_token_stored_under_the_wrong_person_opens_nothing():
    """Maya's token in Priya's file must not let Priya (or anyone) read as Maya."""
    w = DmWorld(authorized=("dana", "maya"))
    w.tokens = UserTokens({EMAILS["priya"]: w.grant("priya", token_of="maya"), EMAILS["dana"]: w.grant("dana"),
                           EMAILS["maya"]: w.grant("maya")})
    w.conn = SlackConnector(w.slack.client(), w.identities, user_tokens=w.tokens)
    mayas = w.dm("maya", "dana")
    priyas = w.dm("priya", "dana")
    assert w.crawl() & {mayas, priyas} == {mayas}        # Priya is not validly authorized, so her DM is no document
    assert not w.allowed("priya", mayas) and not w.allowed("priya", priyas)


# -- changes --------------------------------------------------------------------------------------
def test_new_messages_and_new_dms_are_upserts(w):
    doc_id = w.dm("priya", "dana")
    _, cursor = w.changes(None)
    channel, root = doc_id.split(":")[1].split("/")
    w.slack.post(channel, w.user("dana"), "Understood.", thread_ts=root)
    later = w.dm("dana", "maya", text="Lunch?")
    changes, _ = w.changes(cursor)
    assert changes == [("upsert", doc_id, None, None), ("upsert", later, None, None)]
    assert w.conn.fetch(doc_id).body == SECRET + "\n\nUnderstood."


def test_withdrawing_deletes_that_persons_dms_and_denies_everyone(w):
    with_dana, without = w.dm("priya", "dana"), w.dm("priya", "maya")
    _, cursor = w.changes(None)
    w.slack.revoked.add(w.user("dana"))                  # Dana removes the app in Slack
    assert not w.allowed("dana", with_dana)              # at once, before any scan
    changes, cursor = w.changes(cursor)
    assert changes == [("delete", with_dana, None, None)]
    assert _denied_like_nothing(w, "priya", with_dana) and w.allowed("priya", without)
    with pytest.raises(DocumentNotFound):
        w.conn.fetch(with_dana)
    assert w.changes(cursor)[0] == []


def test_a_fetch_that_meets_a_withdrawn_token_finds_no_document(w):
    doc_id = w.dm("dana", "priya")
    w.crawl()
    w.slack.revoked.add(w.user("dana"))
    with pytest.raises(DocumentNotFound):                # before the scan that would report the delete
        w.conn.fetch(doc_id)


def test_a_failure_while_reading_dms_is_not_a_withdrawal(w):
    doc_id = w.dm("priya", "dana")
    _, cursor = w.changes(None)
    w.slack.down = True
    with pytest.raises(Exception):                       # noqa: B017, PT011  (any transport error)
        w.conn.list_changes(cursor)
    w.slack.down = False
    assert w.changes(cursor)[0] == [] and w.allowed("priya", doc_id)


def test_dm_events_are_quick_and_a_dm_that_has_just_begun_makes_the_pass_scan(w):
    doc_id = w.dm("priya", "dana")
    _, cursor = w.changes(None)
    channel, root = doc_id.split(":")[1].split("/")
    w.slack.post(channel, w.user("dana"), "Understood.", thread_ts=root)
    scans = w.slack.calls["conversations.history"]
    w.conn.note_thread(channel, root)
    w.conn.quick_next()
    changes, cursor = w.changes(cursor)
    assert changes == [("upsert", doc_id, None, None)] and w.slack.calls["conversations.history"] == scans   # no scan

    begun = w.dm("dana", "maya", text="Lunch?")
    w.conn.note_thread(*begun.split(":")[1].split("/"))
    w.conn.quick_next()
    changes, _ = w.changes(cursor)
    assert ("upsert", begun, None, None) in changes and w.slack.calls["conversations.history"] > scans


def test_a_new_process_keeps_the_dms_from_its_cursor(w):
    kept, gone = w.dm("priya", "dana"), w.dm("priya", "maya")
    _, cursor = w.changes(None)
    channel, root = gone.split(":")[1].split("/")
    w.slack.delete(channel, root)
    fresh = SlackConnector(w.slack.client(), w.identities, user_tokens=w.tokens)
    changes = [(c.type, c.doc_id) for c in fresh.list_changes(cursor).changes]
    assert ("upsert", kept) in changes and ("delete", gone) in changes


def test_ingestion_indexes_a_dm_under_its_participants_and_removes_it_on_withdrawal(w):
    doc_id = w.dm("priya", "dana")
    store = InMemoryStore()
    ingestor = Ingestor([w.conn], store, FakeEmbedder())
    ingestor.run_once()
    chunks = store.chunks_of(doc_id)
    assert chunks and all(c.acl_tokens == ["user:dana@companya.com", "user:priya@companya.com"] for c in chunks)
    w.slack.revoked.add(w.user("priya"))
    ingestor.run_once()
    assert store.chunks_of(doc_id) == []


# -- the stored grants ----------------------------------------------------------------------------
def _write(directory, email: str, user_id: str, token: str) -> None:
    token_path(email, directory).write_text(json.dumps({"canonical_email": email, "user_id": user_id, "access_token": token}),
                                            encoding="utf-8")


def test_user_tokens_are_loaded_from_their_files_and_followed_as_they_change(tmp_path):
    made: list[str] = []
    tokens = UserTokens(directory=tmp_path, client_for=lambda token: made.append(token) or object())
    assert len(tokens) == 0 and tokens.get("priya@companya.com") is None
    assert token_path("Priya@companya.com", tmp_path).name == "token-slack-priya_at_companya.com.json"

    _write(tmp_path, "priya@companya.com", "U1", "xoxp-one")
    _write(tmp_path, "dana@companya.com", "U2", "xoxb-a-bot-token")                    # not a user token
    (tmp_path / "token-slack-maya_at_companya.com.json").write_text("{not json", encoding="utf-8")
    token_path("jordan@companya.com", tmp_path).write_text(                           # names someone else
        json.dumps({"canonical_email": "priya@companya.com", "user_id": "U4", "access_token": "xoxp-four"}), encoding="utf-8")
    tokens.refresh()
    assert [(g.email, g.user_id) for g in tokens.all()] == [("priya@companya.com", "U1")] and made == ["xoxp-one"]

    tokens.refresh()
    assert made == ["xoxp-one"]                           # unchanged file: the same client is kept
    tokens.drop("priya@companya.com")
    tokens.refresh()
    assert len(tokens) == 0                               # a dropped token stays dropped while its file is unchanged
    _write(tmp_path, "priya@companya.com", "U1", "xoxp-one-again")                    # authorized again
    tokens.refresh()
    assert tokens.get("PRIYA@companya.com") is not None and made[-1] == "xoxp-one-again"
    token_path("priya@companya.com", tmp_path).unlink()
    tokens.refresh()
    assert len(tokens) == 0


def test_the_token_directory_can_be_set_for_a_deployment(tmp_path, monkeypatch):
    monkeypatch.setenv("SLACK_USER_TOKENS_DIR", str(tmp_path))
    _write(tmp_path, "priya@companya.com", "U1", "xoxp-one")
    assert [g.email for g in UserTokens().all()] == ["priya@companya.com"]


# -- authorizing ----------------------------------------------------------------------------------
def test_the_consent_page_asks_for_the_four_read_scopes_as_the_user_and_nothing_else():
    url = consent_url("123.456", "https://localhost/slack/oauth", "state-1", "T1")
    assert url.startswith("https://slack.com/oauth/v2/authorize?")
    assert "user_scope=im%3Ahistory%2Cim%3Aread%2Cmpim%3Ahistory%2Cmpim%3Aread" in url
    assert "&scope=" not in url and "?scope=" not in url and "write" not in url
    assert "state=state-1" in url and "team=T1" in url


def test_the_pasted_address_must_answer_this_request():
    assert code_from("https://localhost/slack/oauth?code=abc&state=s1", "s1") == "abc"
    for address in ("https://localhost/slack/oauth?code=abc&state=other", "https://localhost/slack/oauth?state=s1",
                    "https://localhost/slack/oauth?error=access_denied&state=s1", "abc"):
        with pytest.raises(SystemExit):
            code_from(address, "s1")


def test_a_code_is_traded_once_for_that_persons_user_token(w):
    w.slack.codes["code-1"] = w.user("priya")
    token, user_id, team = exchange(w.slack.client(), "code-1", "id", "secret", "https://localhost/slack/oauth")
    assert (token, user_id, team) == (USER_TOKEN_PREFIX + w.user("priya"), w.user("priya"), "T0COMPANYA")
    with pytest.raises(SystemExit, match="invalid_code"):
        exchange(w.slack.client(), "code-1", "id", "secret", "https://localhost/slack/oauth")

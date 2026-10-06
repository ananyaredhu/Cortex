"""The Drive connector against the in-memory fake.

The shared contract tests (connectors/tests/contract) cover the seeded story, inheritance and restriction. These
cover what is specific to Drive on personal accounts: the root-folder boundary, who can read a sharing list,
groups declared in config, unmapped and public sharing, content, rate limits and failure.
"""
import copy
import time
from pathlib import Path

import httpx
import pytest

from connectors.base import DocumentNotFound, PlatformIdentity
from connectors.gdrive import DriveConfig, DriveConnector, DriveError, Group
from connectors.gdrive.authorize import consent_url, exchange, token_path
from connectors.gdrive.check_setup import report
from connectors.gdrive.client import load_sessions
from connectors.gdrive.fake import ADMIN, API, TOKEN, FakeDrive
from connectors.gdrive.testing import SeededDrive
from connectors.ingestion import FakeEmbedder, Ingestor, InMemoryStore
from fixtures.loader import load

DATA = load()
POSTMORTEM, VENDOR = "gdrive:postmortem-pay-outage", "gdrive:vendor-integration-notes"


@pytest.fixture
def d() -> SeededDrive:
    return SeededDrive()


def account(persona: str) -> str:
    return f"{persona}.account@example.com"


def allowed(conn, persona_email: str, doc_id: str) -> bool:
    return conn.check_access(conn.resolve_identity(persona_email), doc_id).allowed


def changes(conn, cursor):
    batch = conn.list_changes(cursor)
    return [(c.type, c.doc_id) for c in batch.changes], batch.next_cursor


# -- documents ----------------------------------------------------------------------------------
def test_document_shape(d):
    doc = d.fetch(POSTMORTEM)
    fixture = next(x for x in DATA["documents"] if x["doc_id"] == POSTMORTEM)
    assert (doc.kind, doc.title, doc.parent_id, doc.version) == ("file", fixture["title"], "gdrive:folder-incidents", "1")
    assert doc.updated_at == fixture["updated_at"]
    assert doc.body == fixture["body"], "exported text: byte-order mark and Windows line endings removed"
    assert doc.author is None, "the owner is not in the identity map"
    assert doc.acl.tokens == ["group:gdrive:payments-eng", "user:dana@companya.com"]
    assert doc.acl.native == {"folder": "folder-incidents", "inferred_groups": [], "sharing": [
        {"principal": "dana@companya.com", "role": "reader", "type": "user"},
        {"principal": "payments-eng", "role": "reader", "type": "group"},
        {"principal": None, "role": "owner", "type": "user"}]}
    assert ADMIN not in str(doc.acl.native) and "example.com" not in str(doc.acl.native), "no real address is kept as evidence"


def test_files_that_are_not_text_have_no_body_and_folders_are_not_documents(d):
    d.drive.add("scan-0001", "Scanned contract.pdf", owner=ADMIN, parent="folder-vendor", mime="application/pdf", content="%PDF")
    d.drive.add("notes-0001", "notes.txt", owner=ADMIN, parent="folder-vendor", mime="text/plain", content="plain notes")
    assert d.fetch("gdrive:scan-0001").body == "" and d.fetch("gdrive:notes-0001").body == "plain notes"
    with pytest.raises(DocumentNotFound):
        d.fetch("gdrive:folder-vendor")
    assert not allowed(d, "priya@companya.com", "gdrive:folder-vendor")


# -- the boundary -------------------------------------------------------------------------------
def test_a_file_is_read_through_an_account_that_can_see_its_folder(d):
    """Drive names a file's folder only to accounts that can see that folder. Maya is shared one file in the vendor
    folder but not the folder, so her answer has no parent; asking her first must not put the file out of scope."""
    d.drive.add("maya-note", "Note for Maya", owner=ADMIN, parent="folder-vendor", content="hello Maya")
    d.drive.share("maya-note", account("maya"))
    maya_first = {"maya@companya.com": d._sessions["maya@companya.com"], "admin": d._sessions["admin"]}
    conn = DriveConnector(maya_first, d.identities, d.config)
    doc = conn.fetch("gdrive:maya-note")
    assert (doc.parent_id, doc.body) == ("gdrive:folder-vendor", "hello Maya")
    assert allowed(conn, "maya@companya.com", "gdrive:maya-note") and allowed(conn, "priya@companya.com", "gdrive:maya-note")
    assert not allowed(conn, "jordan@companya.com", "gdrive:maya-note")


def test_nothing_outside_the_root_folders_exists(d):
    """The persona accounts are people's own Google accounts. Their other files must stay invisible."""
    d.drive.add("private-diary", "My diary", owner=account("priya"), content="not Company A's business")
    d.drive.add_folder("personal-folder", "Personal", owner=account("priya"))
    d.drive.add("private-taxes", "Taxes", owner=account("priya"), parent="personal-folder", content="also private")
    crawl, _ = changes(d, None)
    assert sorted(doc for _, doc in crawl) == [POSTMORTEM, VENDOR]
    for doc_id in ("gdrive:private-diary", "gdrive:private-taxes"):
        with pytest.raises(DocumentNotFound):
            d.fetch(doc_id)
        with pytest.raises(DocumentNotFound):
            d.version(doc_id)
        decision = d.check_access(d.resolve_identity("priya@companya.com"), doc_id)
        assert (decision.allowed, decision.proof_path, decision.acl_snapshot_hash) == (False, [], "")


def test_subfolders_of_a_root_are_in_scope_and_inherit(d):
    d.drive.add_folder("folder-2026", "2026", owner=ADMIN, parent="folder-incidents")
    d.drive.add("nested-0001", "October incident", owner=ADMIN, parent="folder-2026", content="nested")
    assert ("upsert", "gdrive:nested-0001") in changes(d, None)[0]
    assert d.fetch("gdrive:nested-0001").acl.tokens == ["group:gdrive:payments-eng"], "inherited from the folder two levels up"
    assert allowed(d, "priya@companya.com", "gdrive:nested-0001") and not allowed(d, "maya@companya.com", "gdrive:nested-0001")


def test_no_root_folders_means_nothing_is_read(d):
    empty = DriveConnector(d._sessions, d.identities, DriveConfig())
    assert empty.list_changes(None).changes == []
    with pytest.raises(DocumentNotFound):
        empty.fetch(POSTMORTEM)
    assert not allowed(empty, "dana@companya.com", POSTMORTEM)


# -- who may read -------------------------------------------------------------------------------
def test_proof_paths_name_the_grant(d):
    def proof(email, doc_id):
        return d.check_access(d.resolve_identity(email), doc_id).proof_path

    assert proof("priya@companya.com", POSTMORTEM) == ["user:priya@companya.com", "group:gdrive:payments-eng"]
    assert proof("dana@companya.com", POSTMORTEM) == ["user:dana@companya.com"], "shared with her directly"
    assert proof("sam@contractor.io", VENDOR) == ["user:sam@contractor.io", "external:sam@contractor.io"]
    assert proof("maya@companya.com", POSTMORTEM) == []
    assert d.resolve_identity("sam@contractor.io").groups == ["external:sam@contractor.io"]
    assert d.resolve_identity("priya@companya.com").groups == ["group:gdrive:payments-eng"]


def test_group_shared_one_by_one_is_recognised_from_config(d):
    """Personal accounts often have no Google Group: the file is shared with each member instead."""
    d.config.groups["payments-eng"] = Group(d.config.groups["payments-eng"].members)    # no group address any more
    d.drive.add_folder("folder-plans", "plans", owner=ADMIN)
    d.config.root_folders.append("folder-plans")
    d.drive.add("plan-0001", "Capacity plan", owner=ADMIN, parent="folder-plans", content="plan")
    d.drive.share("plan-0001", account("priya"))
    assert d.fetch("gdrive:plan-0001").acl.tokens == ["user:priya@companya.com"], "only one of the two members so far"
    d.drive.share("plan-0001", account("dana"))
    acl = d.fetch("gdrive:plan-0001").acl
    assert acl.tokens == ["group:gdrive:payments-eng"] and acl.native["inferred_groups"] == ["payments-eng"]
    assert d.check_access(d.resolve_identity("dana@companya.com"), "gdrive:plan-0001").proof_path == [
        "user:dana@companya.com", "group:gdrive:payments-eng"]
    assert not allowed(d, "maya@companya.com", "gdrive:plan-0001")


def test_a_new_group_member_in_config_does_not_gain_access_to_files_shared_one_by_one(d):
    d.config.groups["payments-eng"] = Group(d.config.groups["payments-eng"].members | {"maya@companya.com"})
    assert "group:gdrive:payments-eng" in d.resolve_identity("maya@companya.com").groups
    d.drive.files["folder-vendor"]["permissions"] = []          # the vendor folder is no longer shared with the Google Group
    d.drive.share("vendor-integration-notes", account("priya"))
    d.drive.share("vendor-integration-notes", account("dana"))
    assert not allowed(d, "maya@companya.com", VENDOR), "Drive never shared it with her, whatever the config says"
    assert "group:gdrive:payments-eng" not in d.fetch(VENDOR).acl.tokens, "not every member has it, so no group token"


def test_unmapped_accounts_and_link_sharing_give_no_token(d):
    d.drive.share("vendor-integration-notes", "stranger@elsewhere.example")
    d.drive.share("vendor-integration-notes", kind="anyone")
    d.drive.share("vendor-integration-notes", "unknown-group@groups.example.com", kind="group")
    acl = d.fetch(VENDOR).acl
    assert acl.tokens == ["external:sam@contractor.io", "group:gdrive:payments-eng"]
    assert {"principal": None, "role": "reader", "type": "anyone"} in acl.native["sharing"]
    assert "stranger" not in str(acl.native)
    assert not allowed(d, "jordan@companya.com", VENDOR), "anyone-with-the-link is not a grant we honour"


def test_identity_must_match_the_mapped_account(d):
    dana = d.resolve_identity("dana@companya.com")
    assert d.check_access(dana, POSTMORTEM).allowed
    spoofed = PlatformIdentity("gdrive", account("dana"), "maya@companya.com", ["group:gdrive:payments-eng"])
    assert not d.check_access(spoofed, POSTMORTEM).allowed
    assert not d.check_access(PlatformIdentity("slack", account("dana"), "dana@companya.com", []), POSTMORTEM).allowed
    assert d.resolve_identity("ghost@nowhere.example") is None


def test_a_file_whose_sharing_nobody_signed_in_can_read_is_not_indexed(d):
    """Drive shows the sharing list only to people who can share the file. Viewers alone are not enough."""
    viewers_only = {k: v for k, v in d._sessions.items() if k != "admin"}
    conn = DriveConnector(viewers_only, d.identities, d.config)
    assert conn.list_changes(None).changes == []
    with pytest.raises(DocumentNotFound):
        conn.fetch(POSTMORTEM)
    assert not allowed(conn, "dana@companya.com", POSTMORTEM), "no evidence, no access"
    d.drive.share("postmortem-pay-outage", account("dana"), role="writer")        # an editor can read the sharing list
    assert [c.doc_id for c in conn.list_changes(None).changes] == [POSTMORTEM]
    assert allowed(conn, "dana@companya.com", POSTMORTEM)


# -- the change feed ----------------------------------------------------------------------------
def test_edit_unshare_and_trash(d):
    _, cursor = changes(d, None)
    assert changes(d, cursor) == ([], cursor)
    d.drive.edit("postmortem-pay-outage", "Revised postmortem.", "2026-10-11T09:00:00Z")
    found, cursor = changes(d, cursor)
    assert found == [("upsert", POSTMORTEM)] and d.version(POSTMORTEM) == "2"
    assert d.fetch(POSTMORTEM).body == "Revised postmortem."
    d.drive.edit("postmortem-pay-outage", "Revised again, same editing session.")   # Docs leaves the modified time
    found, cursor = changes(d, cursor)
    assert found == [("upsert", POSTMORTEM)] and d.version(POSTMORTEM) == "3"
    assert d.fetch(POSTMORTEM).updated_at == "2026-10-11T09:00:00Z"

    before = d.fetch(VENDOR).acl
    d.drive.unshare("vendor-integration-notes", account("sam"))
    found, cursor = changes(d, cursor)
    assert found == [("acl_change", VENDOR)], "a sharing change is reported as one, whatever the version does"
    after = d.fetch(VENDOR).acl
    assert after.tokens == ["group:gdrive:payments-eng"] and after.snapshot_hash != before.snapshot_hash
    assert not allowed(d, "sam@contractor.io", VENDOR)

    d.drive.files["vendor-integration-notes"]["trashed"] = True
    assert changes(d, cursor)[0] == [("delete", VENDOR)]
    with pytest.raises(DocumentNotFound):
        d.fetch(VENDOR)
    assert not allowed(d, "priya@companya.com", VENDOR)


def test_a_cursor_from_another_process_resyncs(d):
    _, cursor = changes(d, None)
    restarted = DriveConnector(d._sessions, d.identities, d.config)
    again = restarted.list_changes(cursor)
    assert sorted(c.doc_id for c in again.changes) == [POSTMORTEM, VENDOR] and again.next_cursor != cursor


# -- rate limits, expiry and failure ------------------------------------------------------------
def test_backs_off_when_told_to_and_refreshes_an_expired_token():
    waits: list[float] = []
    drive = FakeDrive()
    drive.add("file-00001", "A file", owner="owner@example.com", content="x")
    session = drive.session("owner@example.com", sleep=waits.append)
    drive.throttle = 2
    assert session.json("files/file-00001")["name"] == "A file" and waits == [2.0, 2.0]
    drive.throttle = 1
    with pytest.raises(DriveError) as caught:
        session.json("files/file-00001", retry=False)
    assert caught.value.status == 429 and not caught.value.not_visible and waits == [2.0, 2.0]
    tokens_before = drive.calls["token"]
    drive.expire_tokens = 1
    assert session.json("files/file-00001")["id"] == "file-00001" and drive.calls["token"] == tokens_before + 1
    with pytest.raises(DriveError) as caught:
        session.json("files/nope-00000")
    assert caught.value.not_visible


def test_query_path_fails_closed_on_rate_limits_outage_and_slowness(d):
    dana = d.resolve_identity("dana@companya.com")
    d.drive.throttle = 1
    assert not d.check_access(dana, POSTMORTEM).allowed
    assert d.check_access(dana, POSTMORTEM).allowed
    d.drive.delay = 0.2
    started = time.perf_counter()
    assert d.check_access(dana, POSTMORTEM).allowed and time.perf_counter() - started < 0.7
    hurried = DriveConnector(d._sessions, d.identities, d.config, access_timeout=0.05)
    assert not hurried.check_access(dana, POSTMORTEM).allowed
    d.drive.delay = 0.0
    d.drive.down = True
    decision = d.check_access(dana, POSTMORTEM)
    assert (decision.allowed, decision.proof_path) == (False, [])
    with pytest.raises(httpx.ConnectError):
        d.list_changes(None)


# -- configuration and sign-in ------------------------------------------------------------------
def test_config_loading(tmp_path, monkeypatch):
    assert DriveConfig.load(tmp_path / "missing.json") == DriveConfig()
    path = tmp_path / "gdrive.local.json"
    path.write_text('{"root_folders": [" abc123 "], "groups": {"payments-eng": ["Priya@CompanyA.com"], '
                    '"sec": {"members": ["dana@companya.com"], "address": "Sec@Groups.Example.com"}}, "org_domains": ["@CompanyA.com"]}',
                    encoding="utf-8")
    monkeypatch.setenv("GDRIVE_CONFIG_PATH", str(path))
    config = DriveConfig.load()
    assert config.root_folders == ["abc123"] and config.groups_of("priya@companya.com") == ["payments-eng"]
    assert config.group_by_address("sec@groups.example.com") == "sec" and config.group_by_address("other@example.com") is None
    assert config.is_internal("dana@companya.com") and not config.is_internal("sam@contractor.io")
    for bad in ("{not json", "[]", '{"groups": {"g": 5}}', '{"root_folders": "abc"}'):
        path.write_text(bad, encoding="utf-8")
        with pytest.raises(ValueError, match="gdrive config"):
            DriveConfig.load()
    example = DriveConfig.load(Path(__file__).resolve().parents[1] / "gdrive.example.json")
    assert example.groups_of("dana@companya.com") == ["payments-eng"] and example.org_domains == ["companya.com"]


def test_sign_in_exchange_and_token_files(tmp_path, monkeypatch):
    url = consent_url("client-id", "http://127.0.0.1:5555", "state123")
    assert "drive.readonly" in url and "access_type=offline" in url and "state=state123" in url and "secret" not in url

    def google(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith(TOKEN):
            return httpx.Response(200, json={"access_token": "at:p", "refresh_token": "rt:priya.account@example.com"})
        return httpx.Response(200, json={"user": {"emailAddress": "Priya.Account@example.com"}})

    http = httpx.Client(transport=httpx.MockTransport(google))
    refresh, signed_in = exchange(http, "code", "client-id", "client-secret", "http://127.0.0.1:5555", token_url=TOKEN, api_url=API)
    assert (refresh, signed_in) == ("rt:priya.account@example.com", "priya.account@example.com")
    assert token_path("Priya@companya.com").name == "token-gdrive-priya_at_companya.com.json"

    (tmp_path / "token-gdrive-priya_at_companya.com.json").write_text(
        '{"canonical_email": "Priya@companya.com", "refresh_token": "rt:x"}', encoding="utf-8")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "client-secret")
    assert list(load_sessions(tmp_path)) == ["priya@companya.com"]
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET")
    with pytest.raises(RuntimeError, match="GOOGLE_OAUTH_CLIENT_ID"):
        load_sessions(tmp_path)


def test_setup_check_compares_drive_with_the_fixtures(d):
    lines, problems = report(d, DATA)
    assert len(lines) == 2 and problems == []
    d.drive.unshare("vendor-integration-notes", account("sam"))
    d.drive.files["postmortem-pay-outage"]["name"] = "Postmortem (draft)"
    lines, problems = report(d, DATA)
    assert len(problems) == 2 and "no readable file titled" in problems[0] and "readable by ['dana', 'priya']" in problems[1]
    assert not any(doc["body"] in text for text in lines + problems for doc in DATA["documents"]), "file text is never printed"


def test_setup_check_accepts_other_tokens_that_let_in_the_same_people(d):
    """On real Drive, Dana shared one by one and also in payments-eng comes out as the group token alone."""
    data = copy.deepcopy(DATA)
    vendor = next(x for x in data["documents"] if x["doc_id"] == VENDOR)
    vendor["acl"]["tokens"] = ["external:sam@contractor.io", "group:gdrive:payments-eng", "user:dana@companya.com"]
    assert report(d, data)[1] == []


# -- into the index -----------------------------------------------------------------------------
def test_ingestion_indexes_drive_and_follows_an_unshare(d):
    store = InMemoryStore()
    ingestor = Ingestor([d], store, FakeEmbedder())
    ingestor.run_once()
    assert store.live_doc_ids("gdrive") == {POSTMORTEM, VENDOR}
    assert store.chunks_of(VENDOR)[0].acl_tokens == ["external:sam@contractor.io", "group:gdrive:payments-eng"]
    d.drive.unshare("vendor-integration-notes", account("sam"))
    report_ = ingestor.run_once()
    assert dict(report_.actions["gdrive"]) == {"acl_rewritten": 1}
    assert store.chunks_of(VENDOR)[0].acl_tokens == ["group:gdrive:payments-eng"]
    assert [(e.kind, e.doc_id) for e in store.events_after(0)] == [("acl_change", VENDOR)]

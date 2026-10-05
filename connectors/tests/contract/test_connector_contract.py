"""Shared contract tests (docs/02-contracts/connector-interface.md v0.2, "Contract tests").

Every connector, real, simulated or fixture, must pass these. Register a new connector by adding
`name: (factory, [sources it serves])` to CONNECTORS. A factory returns a *seeded* connector for one source
plus an `advance(event_id)` hook that applies a scripted change (for real connectors, drive the real API or
the simulator admin endpoint).

Backends that can change permissions also expose two optional hooks:
- `revoke_container(container_id, token)`: take a grant off the container (space, project, folder, channel).
- `restrict_document(doc_id, token)`: restrict one document to the principal that `token` names.
A backend without a hook is an expected failure for the test that needs it.

A real platform assigns its own IDs and versions. Such a backend translates IDs through its seed manifest
and exposes `seed_version(doc)`, the version the seeded document has there.
Tests that a given backend cannot support yet must be marked `xfail` with a reason, never deleted.
"""
import pytest

from connectors.base import Change, DocumentNotFound, PlatformIdentity
from connectors.gdrive.testing import SeededDrive
from connectors.slack.testing import SeededSlack
from connectors.stub.fixture_connector import FixtureConnector
from fixtures.loader import load, persona_by_id
from simulators.confluence.testing import SeededConfluence
from simulators.jira.testing import SeededJira

DATA = load()
SOURCES = ["slack", "gdrive", "confluence", "jira"]

# name -> (factory(source) -> connector exposing advance(event_id), sources that backend serves)
CONNECTORS = {
    "fixture": (lambda source: FixtureConnector(source), SOURCES),
    "confluence-sim": (lambda source: SeededConfluence(), ["confluence"]),
    "jira-sim": (lambda source: SeededJira(), ["jira"]),
    "slack-fake": (lambda source: SeededSlack(), ["slack"]),   # the real connector over an in-memory Slack
    "gdrive-fake": (lambda source: SeededDrive(), ["gdrive"]),   # the real connector over an in-memory Drive
}


@pytest.fixture(params=[(name, s) for name, (_, sources) in CONNECTORS.items() for s in sources],
                ids=lambda p: f"{p[0]}-{p[1]}")
def conn(request):
    name, source = request.param
    return CONNECTORS[name][0](source)


def _docs_of(conn):
    return [d for d in DATA["documents"] if d["source"] == conn.source]


def _readers(doc):
    return [p for p in DATA["personas"] if set(p["tokens"]) & set(doc["acl"]["tokens"])]


def _drain(conn, cursor=None) -> tuple[list[Change], str]:
    """Follow `list_changes` from `cursor` until it has no more. Returns (all changes, the cursor to resume from)."""
    changes: list[Change] = []
    while True:
        batch = conn.list_changes(cursor)
        changes += batch.changes
        cursor = batch.next_cursor
        if not batch.has_more:
            return changes, cursor


def _allowed(conn, persona, doc) -> bool:
    return conn.check_access(conn.resolve_identity(persona["email"]), doc["doc_id"]).allowed


# 1. Seeded fixture ---------------------------------------------------------------------------------
def test_fetch_and_version_match_seed(conn):
    for d in _docs_of(conn):
        got = conn.fetch(d["doc_id"])
        assert got.doc_id == d["doc_id"]
        expected_version = conn.seed_version(d) if hasattr(conn, "seed_version") else d["version"]
        assert got.version == conn.version(d["doc_id"]) == expected_version
        assert got.acl.tokens == d["acl"]["tokens"]
        assert got.acl.snapshot_hash.startswith("sha256:")


@pytest.mark.parametrize("persona_id", [p["id"] for p in DATA["personas"]])
def test_check_access_matches_expected_visibility(conn, persona_id):
    persona = persona_by_id(DATA, persona_id)
    ident = conn.resolve_identity(persona["email"])
    if ident is None:
        # No account on this platform (Sam on Slack): acceptable only for someone who may read nothing here.
        assert not any(set(persona["tokens"]) & set(d["acl"]["tokens"]) for d in _docs_of(conn)), persona_id
        return
    assert ident.source == conn.source
    assert set(ident.groups) <= set(persona["tokens"]), "identity groups are ACL tokens the persona really holds"
    for d in _docs_of(conn):
        expected = bool(set(persona["tokens"]) & set(d["acl"]["tokens"]))
        dec = conn.check_access(ident, d["doc_id"])
        assert dec.allowed is expected, (persona_id, d["doc_id"])
        assert dec.policy_version
        if dec.allowed:
            assert dec.proof_path, "an allowed decision needs a proof path"


def test_unknown_identity_fails_closed(conn):
    assert conn.resolve_identity("ghost@nowhere.example") is None
    ghost = PlatformIdentity(conn.source, "nobody", "ghost@nowhere.example", [])
    for d in _docs_of(conn):
        assert conn.check_access(ghost, d["doc_id"]).allowed is False


# 2. Revocation -------------------------------------------------------------------------------------
def test_membership_revocation_flips_access_and_emits_one_principal_change(conn):
    """A person loses a membership: access flips at once, and the feed says who and which token, not which documents."""
    ev = next(e for e in DATA["events"] if e["type"] == "acl_change")
    affected = [d for d in _docs_of(conn) if set(ev["remove_tokens"]) & set(d["acl"]["tokens"])]
    if not affected:
        pytest.skip("the scripted revocation does not touch this source")
    persona = persona_by_id(DATA, ev["persona"])
    assert all(_allowed(conn, persona, d) for d in affected)
    _, cursor = _drain(conn)
    conn.advance(ev["id"])
    assert not any(_allowed(conn, persona, d) for d in affected)
    changes, _ = _drain(conn, cursor)
    assert [(c.type, c.doc_id, c.principal, c.token) for c in changes] == [
        ("principal_change", None, f"user:{persona['email']}", token) for token in ev["remove_tokens"]]
    for d in affected:
        assert conn.fetch(d["doc_id"]).acl.tokens == d["acl"]["tokens"], "the documents' own ACLs did not change"


def test_restricting_a_document_emits_acl_change_and_new_tokens(conn):
    """A document's own ACL changes: one reader keeps access, another loses it, and the feed names the document."""
    if not hasattr(conn, "restrict_document"):
        pytest.xfail("this backend cannot restrict a single document (needs a simulator or a real connector)")
    doc = next(d for d in _docs_of(conn) if len(_readers(d)) >= 2)
    keeps, loses = _readers(doc)[:2]
    token = f"user:{keeps['email']}"
    _, cursor = _drain(conn)
    conn.restrict_document(doc["doc_id"], token)
    assert _allowed(conn, keeps, doc) and not _allowed(conn, loses, doc)
    assert conn.fetch(doc["doc_id"]).acl.tokens == [token]
    changes, _ = _drain(conn, cursor)
    assert any(c.type == "acl_change" and c.doc_id == doc["doc_id"] for c in changes)
    assert not any(c.type == "principal_change" for c in changes)


# 3. Edit -------------------------------------------------------------------------------------------
def test_edit_changes_version_and_emits_upsert(conn):
    target = next((e for e in DATA["events"] if e["type"] == "upsert"
                   and e["doc_id"].split(":")[0] == conn.source), None)
    if target is None:
        pytest.skip("no scripted edit for this source")
    before = conn.version(target["doc_id"])
    _, cursor = _drain(conn)
    conn.advance(target["id"])
    assert conn.version(target["doc_id"]) != before
    changes, _ = _drain(conn, cursor)
    assert any(c.type == "upsert" and c.doc_id == target["doc_id"] for c in changes)


# 4. Inheritance ------------------------------------------------------------------------------------
def test_container_permissions_inherit_to_children(conn):
    """Take a grant away at the container (space, folder, project): the child loses it, and says so."""
    if not hasattr(conn, "revoke_container"):
        pytest.xfail("this backend cannot change container permissions (needs a simulator or a real connector)")
    doc, persona, token = next(
        (d, p, t) for d in _docs_of(conn) for p in DATA["personas"]
        for t in sorted(set(p["tokens"]) & set(d["acl"]["tokens"])) if not t.startswith("user:"))
    assert _allowed(conn, persona, doc)
    _, cursor = _drain(conn)
    conn.revoke_container(doc["parent_id"], token)
    assert _allowed(conn, persona, doc) is False
    assert token not in conn.fetch(doc["doc_id"]).acl.tokens
    changes, _ = _drain(conn, cursor)
    assert any(c.type == "acl_change" and c.doc_id == doc["doc_id"] for c in changes)


# 5. Negative ---------------------------------------------------------------------------------------
def test_negative_forbidden_and_nonexistent_look_alike(conn):
    """Forbidden and nonexistent documents must give the same response shape (no existence side channel)."""
    # Sam first (the scenario-3 asker); anyone else who has an account here when Sam has none (Slack).
    personas = sorted(DATA["personas"], key=lambda p: p["id"] != "sam")
    asker, forbidden = next(((ident, d) for p in personas if (ident := conn.resolve_identity(p["email"]))
                             for d in _docs_of(conn) if not set(p["tokens"]) & set(d["acl"]["tokens"])), (None, None))
    if forbidden is None:
        pytest.skip("no document forbidden to anyone with an account in this source")
    a = conn.check_access(asker, forbidden["doc_id"])
    b = conn.check_access(asker, f"{conn.source}:does-not-exist")
    assert a.allowed is False and b.allowed is False
    assert a.proof_path == b.proof_path == []
    assert set(vars(a)) == set(vars(b))


def test_fetch_and_version_raise_document_not_found(conn):
    other = next(d["doc_id"] for d in DATA["documents"] if d["source"] != conn.source)
    for doc_id in (f"{conn.source}:does-not-exist", other, "garbage"):
        with pytest.raises(DocumentNotFound):
            conn.fetch(doc_id)
        with pytest.raises(DocumentNotFound):
            conn.version(doc_id)


# 6. Initial load -----------------------------------------------------------------------------------
def test_initial_load_lists_every_document_as_an_upsert(conn):
    """`list_changes(None)` is the only way to enumerate a source: it must return everything, once."""
    changes, cursor = _drain(conn)
    assert all(c.type == "upsert" for c in changes)
    assert sorted(c.doc_id for c in changes) == sorted(d["doc_id"] for d in _docs_of(conn))
    assert _drain(conn, cursor) == ([], cursor), "nothing has changed since the crawl"

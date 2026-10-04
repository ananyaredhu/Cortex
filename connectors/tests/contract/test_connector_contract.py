"""Shared contract tests (docs/02-contracts/connector-interface.md, "Contract tests").

Every connector, real, simulated or fixture, must pass these. Register a new connector by adding
`name: (factory, [sources it serves])` to CONNECTORS. A factory returns a *seeded* connector for one source
plus an `advance(event_id)` hook that applies a scripted change (for real connectors, drive the real API or
the simulator admin endpoint). Backends that can change container permissions also expose
`revoke_container(container_id, token)`, which the inheritance test uses.
Tests that a given backend cannot support yet must be marked `xfail` with a reason, never deleted.
"""
import pytest

from connectors.stub.fixture_connector import FixtureConnector
from fixtures.loader import load, persona_by_id
from simulators.confluence.testing import SeededConfluence

DATA = load()
SOURCES = ["slack", "gdrive", "confluence", "jira"]

# name -> (factory(source) -> connector exposing advance(event_id), sources that backend serves)
CONNECTORS = {
    "fixture": (lambda source: FixtureConnector(source), SOURCES),
    "confluence-sim": (lambda source: SeededConfluence(), ["confluence"]),
}


@pytest.fixture(params=[(name, s) for name, (_, sources) in CONNECTORS.items() for s in sources],
                ids=lambda p: f"{p[0]}-{p[1]}")
def conn(request):
    name, source = request.param
    return CONNECTORS[name][0](source)


def _docs_of(conn):
    return [d for d in DATA["documents"] if d["source"] == conn.source]


def test_fetch_and_version_match_seed(conn):
    for d in _docs_of(conn):
        got = conn.fetch(d["doc_id"])
        assert got.doc_id == d["doc_id"]
        assert got.version == conn.version(d["doc_id"]) == d["version"]
        assert got.acl.tokens == d["acl"]["tokens"]
        assert got.acl.snapshot_hash.startswith("sha256:")


@pytest.mark.parametrize("persona_id", [p["id"] for p in DATA["personas"]])
def test_check_access_matches_expected_visibility(conn, persona_id):
    persona = persona_by_id(DATA, persona_id)
    ident = conn.resolve_identity(persona["email"])
    assert ident is not None and ident.source == conn.source
    for d in _docs_of(conn):
        expected = bool(set(persona["tokens"]) & set(d["acl"]["tokens"]))
        dec = conn.check_access(ident, d["doc_id"])
        assert dec.allowed is expected, (persona_id, d["doc_id"])
        assert dec.policy_version
        if dec.allowed:
            assert dec.proof_path, "an allowed decision needs a proof path"


def test_negative_forbidden_and_nonexistent_look_alike(conn):
    """Forbidden and nonexistent documents must give the same response shape (no existence side channel)."""
    sam = conn.resolve_identity("sam@contractor.io")
    forbidden = next((d for d in _docs_of(conn) if not set(persona_by_id(DATA, "sam")["tokens"]) & set(d["acl"]["tokens"])), None)
    if forbidden is None:
        pytest.skip("no document forbidden to Sam in this source")
    a = conn.check_access(sam, forbidden["doc_id"])
    b = conn.check_access(sam, f"{conn.source}:does-not-exist")
    assert a.allowed is False and b.allowed is False
    assert a.proof_path == b.proof_path == []
    assert set(vars(a)) == set(vars(b))


def test_unknown_identity_fails_closed(conn):
    from connectors.base import PlatformIdentity
    ghost = PlatformIdentity(conn.source, "nobody", "ghost@nowhere.example", [])
    for d in _docs_of(conn):
        assert conn.check_access(ghost, d["doc_id"]).allowed is False


def test_edit_changes_version_and_emits_upsert(conn):
    target = next((e for e in DATA["events"] if e["type"] == "upsert"
                   and e["doc_id"].split(":")[0] == conn.source), None)
    if target is None:
        pytest.skip("no scripted edit for this source")
    before = conn.version(target["doc_id"])
    cursor = conn.list_changes(None).next_cursor
    conn.advance(target["id"])
    assert conn.version(target["doc_id"]) != before
    batch = conn.list_changes(cursor)
    assert any(c.type == "upsert" and c.doc_id == target["doc_id"] for c in batch.changes)


def test_revocation_flips_access_and_emits_acl_change(conn):
    ev = next(e for e in DATA["events"] if e["type"] == "acl_change")
    affected = [d for d in _docs_of(conn) if set(ev["remove_tokens"]) & set(d["acl"]["tokens"])]
    if not affected:
        pytest.skip("revocation does not touch this source")
    persona = persona_by_id(DATA, ev["persona"])
    ident = conn.resolve_identity(persona["email"])
    assert all(conn.check_access(ident, d["doc_id"]).allowed for d in affected)
    cursor = conn.list_changes(None).next_cursor
    conn.advance(ev["id"])
    ident_after = conn.resolve_identity(persona["email"])
    assert not any(conn.check_access(ident_after, d["doc_id"]).allowed for d in affected)
    changes = conn.list_changes(cursor).changes
    assert {c.doc_id for c in changes if c.type == "acl_change"} >= {d["doc_id"] for d in affected}


def test_container_permissions_inherit_to_children(conn):
    """Take a grant away at the container (space, folder, project): the child loses it, and says so."""
    if not hasattr(conn, "revoke_container"):
        pytest.xfail("this backend cannot change container permissions (needs a simulator or a real connector)")
    doc, persona, token = next(
        (d, p, t) for d in _docs_of(conn) for p in DATA["personas"]
        for t in sorted(set(p["tokens"]) & set(d["acl"]["tokens"])) if not t.startswith("user:"))
    assert conn.check_access(conn.resolve_identity(persona["email"]), doc["doc_id"]).allowed
    cursor = conn.list_changes(None).next_cursor
    conn.revoke_container(doc["parent_id"], token)
    assert conn.check_access(conn.resolve_identity(persona["email"]), doc["doc_id"]).allowed is False
    assert token not in conn.fetch(doc["doc_id"]).acl.tokens
    assert any(c.type == "acl_change" and c.doc_id == doc["doc_id"] for c in conn.list_changes(cursor).changes)

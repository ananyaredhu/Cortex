"""The UI's search must not leak: `GET /v1/search` lists documents by title and snippet as the person types, so
a title, a snippet or a count taken from a document they cannot open would be a leak, and so would a reply that differs
between "hidden" and "not there".
"""
import json

from fastapi.testclient import TestClient

from brain.api.app import create_app
from brain.config import Settings
from brain.runtime import fixture_runtime
from brain.stub_api.app import app as stub_app
from evals.leakci import FixturePlanter
from fixtures.loader import State, load

H = lambda p: {"Authorization": f"Bearer dev:{p}"}   # noqa: E731
PERSONAS = ("priya", "sam", "dana", "jordan", "maya")
QUERIES = ("database migration blockers replica lag", "security incident report Q3 breach", "auth service threat model",
           "payment-service runbook failover", "DBMIG-142", "quantum hologram audit report")


def _target():
    runtime = fixture_runtime(Settings(floor_latency_ms=0, dev_auth=True))
    return TestClient(create_app(runtime)), FixturePlanter(runtime), runtime


def _search(client, who, q, **params) -> str:
    response = client.get("/v1/search", params={"q": q, **params}, headers=H(who))
    assert response.status_code == 200
    return response.text


def _nothing_hidden(client):
    state = State()
    for who in PERSONAS:
        visible = {d["doc_id"] for d in state.visible_docs(who)}
        for q in QUERIES:
            body = _search(client, who, q, limit=10)
            assert set(json.loads(body)) == {"results"}, "no total, no count, no 'more'"
            assert {h["doc_id"] for h in json.loads(body)["results"]} <= visible, (who, q)
            for doc in load()["documents"]:
                if doc["doc_id"] not in visible:
                    assert doc["doc_id"] not in body and doc["title"] not in body, (who, q, doc["doc_id"])
            if who != "dana":                                   # the breach report, canary included, is hers to read
                assert "CANARY" not in body, (who, q)


def test_no_hit_names_or_quotes_a_document_the_person_cannot_open():
    _nothing_hidden(_target()[0])


def test_the_stub_search_keeps_the_same_rule():
    stub = TestClient(stub_app)
    stub.post("/sim/reset")
    _nothing_hidden(stub)


def test_a_forbidden_and_a_nonexistent_document_give_one_reply():
    client, _, _ = _target()
    forbidden = _search(client, "sam", "security incident report Q3 breach")
    assert forbidden == _search(client, "sam", "quantum hologram audit report") == '{"results":[]}'
    assert json.loads(_search(client, "dana", "security incident report Q3 breach"))["results"]      # it is there, for her


def test_planting_a_hidden_document_changes_nothing_the_person_finds():
    client, planter, _ = _target()
    topic = "Replica lag postmortem nobody may read"
    before = {q: _search(client, "priya", q, limit=10) for q in (*QUERIES, topic)}
    hidden = planter.plant("jira", topic, mode="level", visible_to=())
    planter.ingest()
    assert planter.verify(hidden.doc_id, "priya")["ok"]                           # non-vacuity: indexed, and hidden from her
    assert {q: _search(client, "priya", q, limit=10) for q in before} == before
    planter.remove(hidden.doc_id)
    planter.ingest()


def test_a_revoked_document_leaves_the_results_at_the_next_search():
    client, _, runtime = _target()
    q = "auth service threat model concerns"
    assert "slack:C_AUTHPRIV/thread-1" in _search(client, "priya", q)
    runtime.advance("e2", True)
    assert "slack:C_AUTHPRIV/thread-1" not in _search(client, "priya", q)


def test_a_denied_candidate_is_logged_by_hash_only():
    client, _, runtime = _target()
    runtime.advance("e2", False)                                # revoked at the source; the index still has the old labels
    _search(client, "priya", "auth service threat model concerns")
    event = [e for e in runtime.audit_store.all() if e["event_type"] == "search"][-1]
    assert event["actor"]["client"] == "ui"
    denied = [d for d in event["decisions"] if not d["allowed"]]
    assert denied and all("doc_id" not in d and d["doc_id_hash"].startswith("sha256:") for d in denied)
    assert "C_AUTHPRIV" not in json.dumps(event["decisions"]) and "slack:C_AUTHPRIV/thread-1" not in event["result"]["doc_ids"]

"""The `my_issues` lookup (skills-format 0.3) must not leak: it puts the asker's own issues in front of the model, so an issue
assigned to them that they may not open, and anyone else's issue, must stay out exactly as they do for a search.
"""
import json

from fastapi.testclient import TestClient

from brain.api.app import create_app
from brain.config import Settings
from brain.runtime import fixture_runtime
from evals.leakci import FixturePlanter
from fixtures.loader import State, load

H = lambda p: {"Authorization": f"Bearer dev:{p}"}   # noqa: E731
PERSONAS = ("priya", "sam", "dana", "jordan", "maya")
MINE = "List my open tickets, sorted by urgency"


def _target():
    runtime = fixture_runtime(Settings(floor_latency_ms=0, dev_auth=True))
    return runtime, TestClient(create_app(runtime)), FixturePlanter(runtime)


def _ask(client, who: str, question: str = MINE) -> dict:
    response = client.post("/v1/ask", json={"question": question}, headers=H(who))
    assert response.status_code == 200
    return response.json()


def _same(a: dict, b: dict) -> bool:
    drop = ("request_id", "conversation_id", "freshness")
    return {k: v for k, v in a.items() if k not in drop} == {k: v for k, v in b.items() if k not in drop}


def test_nobody_is_shown_an_issue_they_cannot_open_by_asking_for_their_own():
    _, client, _ = _target()
    state = State()
    for who in PERSONAS:
        out = _ask(client, who)
        assert out["skill"] == "my-work"
        visible = {d["doc_id"] for d in state.visible_docs(who)}
        assert {c["doc_id"] for c in out["citations"]} <= visible, who
        body = json.dumps(out)
        for doc in load()["documents"]:
            if doc["doc_id"] not in visible:
                assert doc["doc_id"] not in body and doc["title"] not in body, (who, doc["doc_id"])


def test_an_issue_assigned_to_me_that_i_may_not_open_changes_nothing():
    """Being the assignee grants nothing: the lookup reads only rows the asker's tokens reach, and the live check follows."""
    runtime, client, planter = _target()
    before = _ask(client, "priya")
    hidden = planter.plant("jira", "Replication tuning nobody may read", mode="level", visible_to=())
    planter.ingest()
    doc = runtime.brain.connectors["jira"].fetch(hidden.doc_id)
    raw = {"doc_id": doc.doc_id, "source": "jira", "kind": doc.kind, "title": doc.title, "url": doc.url,
           "body": doc.body + " Status: To Do. Assignee: Priya.", "parent_id": doc.parent_id, "links": [], "author": doc.author,
           "created_at": doc.updated_at, "updated_at": doc.updated_at, "version": f"{doc.updated_at}#2", "tags": ["leakci"],
           "fields": {"status": "To Do", "assignee": "Priya", "assignee_email": "priya@companya.com", "due": "2026-10-01"},
           "acl": {"tokens": list(doc.acl.tokens), "native": {"holders": "leakci", "mode": "level"},
                   "snapshot_hash": "sha256:leakci-assigned", "observed_at": doc.updated_at}}
    runtime.brain.connectors["jira"].upsert(raw)
    planter.ingest()
    assert planter.verify(hidden.doc_id, "priya")["ok"]                            # non-vacuity: indexed, and hidden from her
    assert dict(runtime.brain.index.document(hidden.doc_id).fields)["assignee_email"] == "priya@companya.com"   # and hers
    after = _ask(client, "priya")
    assert _same(before, after)
    assert hidden.canary not in json.dumps(after) and hidden.doc_id not in json.dumps(after)


def test_someone_with_no_issues_is_refused_exactly_like_a_question_about_nothing():
    """Sam can open no issue. "My tickets" must not say whether issues exist that he cannot see."""
    _, client, _ = _target()
    assert _same(_ask(client, "sam"), _ask(client, "sam", "List my open tickets about the Q9 quantum hologram"))
    assert _ask(client, "sam")["refused"]

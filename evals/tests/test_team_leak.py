"""Team focus must not leak: `GET /v1/team` shows counts and lists, and a count over hidden documents is a leak
. Every number in it must come only from documents the caller may open.
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


def _target():
    runtime = fixture_runtime(Settings(floor_latency_ms=0, dev_auth=True))
    return TestClient(create_app(runtime)), FixturePlanter(runtime)


def _team(client, who) -> str:
    response = client.get("/v1/team", headers=H(who))
    assert response.status_code == 200
    return response.text


def test_nothing_a_person_cannot_open_is_named_or_counted():
    client, _ = _target()
    state = State()
    for who in PERSONAS:
        body = _team(client, who)
        visible = {d["doc_id"] for d in state.visible_docs(who)}
        for doc in load()["documents"]:
            if doc["doc_id"] in visible:
                continue
            assert doc["doc_id"] not in body and doc["title"] not in body, (who, doc["doc_id"])
        assert "CANARY" not in body
        for team in json.loads(body)["teams"]:
            listed = {i["doc_id"] for i in team["issues"]["items"]} | {u["doc_id"] for u in team["updates"]}
            assert listed <= visible, (who, team["project_id"])
            assert team["issues"]["total"] == len(team["issues"]["items"])        # no "of N" beyond what is listed


def test_no_team_looks_the_same_whether_projects_are_hidden_or_absent():
    """Sam can open nothing in any project; Jordan sees public pages of projects and is a member of none."""
    client, _ = _target()
    assert _team(client, "sam") == _team(client, "jordan") == '{"teams":[]}'


def test_planting_a_hidden_issue_in_a_team_project_changes_nothing_the_member_sees():
    client, planter = _target()
    before = _team(client, "priya")
    hidden = planter.plant("jira", "Payment outage follow-up nobody may read", mode="level", visible_to=())
    planter.ingest()
    assert planter.verify(hidden.doc_id, "priya")["ok"]                           # non-vacuity: indexed, and hidden from her
    assert _team(client, "priya") == before
    planter.remove(hidden.doc_id)
    planter.ingest()
    assert _team(client, "priya") == before


def test_a_visible_issue_is_counted_so_the_check_above_is_not_vacuous():
    client, planter = _target()
    total = lambda: next(t for t in json.loads(_team(client, "priya"))["teams"] if t["project_id"] == "PAYINC")["issues"]["total"]  # noqa: E731
    before = total()
    planter.plant("jira", "Payment outage follow-up for Priya", mode="level", visible_to=("priya",))
    planter.ingest()
    assert total() == before + 1


def test_a_hidden_project_adds_no_team():
    client, planter = _target()
    before = {who: _team(client, who) for who in PERSONAS}
    planter.plant("jira", "A project nobody is in", mode="project", visible_to=())
    planter.ingest()
    assert {who: _team(client, who) for who in PERSONAS} == before

"""Team focus (`GET /v1/team`): the registry projects a person is a member of, counted only from documents they may open."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from brain.api.app import create_app
from brain.config import Settings
from brain.projects import ProjectView, Registry, RegistryError, parse
from brain.projects.team import assignee_of, status_of, team_focus
from brain.runtime import fixture_runtime
from brain.stub_api.app import app as stub_app

H = lambda p: {"Authorization": f"Bearer dev:{p}"}   # noqa: E731
PERSONAS = ("priya", "sam", "dana", "jordan", "maya")
PRIVATE = "slack:C_DBMIGPRIV"


@pytest.fixture
def rt():
    return fixture_runtime(Settings(floor_latency_ms=0, dev_auth=True))


@pytest.fixture
def client(rt):
    return TestClient(create_app(rt))


def teams(client, who):
    return {t["project_id"]: t for t in client.get("/v1/team", headers=H(who)).json()["teams"]}


# -- the registry fields ---------------------------------------------------------------------------------------------------
def project(**extra):
    return {"projects": [{"project_id": "A", "name": "Alpha", "aliases": ["alpha"], "components": {"jira": ["AL"]}, **extra}]}


def test_goal_and_target_date_are_optional_and_checked():
    assert parse(project())[0].goal is None and parse(project())[0].target_date is None
    full = parse(project(goal=" Ship it. ", target_date="2026-10-23"))[0]
    assert (full.goal, full.target_date) == ("Ship it.", "2026-10-23")
    for bad in ({"goal": ""}, {"goal": 3}, {"target_date": "23 Oct"}, {"target_date": 20261023}):
        with pytest.raises(RegistryError):
            parse(project(**bad))
    assert all(p.goal and p.target_date and p.start_date and p.milestone for p in Registry.load().projects.values())


def test_timeline_and_sprint_are_optional_and_checked():
    sprint = {"name": "Sprint 42", "start_date": "2026-10-05", "end_date": "2026-10-16", "goal": "Ship."}
    full = parse(project(start_date="2026-09-14", target_date="2026-10-30", milestone={"label": "Cutover", "date": "2026-10-27"},
                         sprint=sprint))[0]
    assert full.milestone == ("Cutover", "2026-10-27") and full.sprint.name == "Sprint 42" and full.sprint.goal == "Ship."
    assert parse(project(sprint={k: v for k, v in sprint.items() if k != "goal"}))[0].sprint.goal is None
    for bad in ({"start_date": "soon"}, {"start_date": "2026-11-01", "target_date": "2026-10-30"},
                {"milestone": {"label": "Cutover"}}, {"milestone": {"label": "", "date": "2026-10-27"}}, {"milestone": "Cutover"},
                {"sprint": {**sprint, "end_date": "2026-10-01"}}, {"sprint": {**sprint, "velocity": 3}},
                {"sprint": {"name": "Sprint 42"}}, {"sprint": "Sprint 42"}):
        with pytest.raises(RegistryError):
            parse(project(**bad))


# -- the pure function -----------------------------------------------------------------------------------------------------
def test_status_is_read_from_the_issue_text():
    assert status_of("Cutover is blocked. Status: In Progress. Blocker owner: platform team.") == "In Progress"
    assert status_of("Tune replication\n\nStatus: To Do\n\nComment by maya: ok") == "To Do"
    assert status_of("No such line here") is None
    assert assignee_of("Blocked. Status: In Progress. Assignee: Maya. Blocker owner: platform team.") == "Maya"
    assert assignee_of("Tune replication\n\nStatus: To Do\n\nAssignee: Anne-Marie O'Neil\n\nComment") == "Anne-Marie O'Neil"
    assert assignee_of("Nobody has it") is None


def row(doc_id, parent, updated="2026-10-10T08:00:00Z", author=None):
    return SimpleNamespace(doc_id=doc_id, source=doc_id.split(":")[0], title=doc_id, url=f"https://x/{doc_id}", parent_id=parent,
                           author=author, updated_at=updated)


def test_counts_cover_only_the_documents_handed_in_and_only_member_projects():
    alpha, beta = parse({"projects": [
        {"project_id": "A", "name": "Alpha", "aliases": ["alpha"], "components": {"jira": ["AL"], "slack": ["C_AL"]},
         "owners": ["lead@x.com"]},
        {"project_id": "B", "name": "Beta", "aliases": ["beta"], "components": {"jira": ["BE"]}}]})
    views = [ProjectView(alpha, ["jira:AL", "slack:C_AL"], True), ProjectView(beta, ["jira:BE"], False)]
    docs = [row("jira:AL-1", "jira:AL"), row("jira:AL-2", "jira:AL", "2026-10-11T08:00:00Z", "lead@x.com"),
            row("slack:C_AL/t1", "slack:C_AL"), row("jira:BE-1", "jira:BE"), row("jira:ZZ-1", "jira:ZZ")]
    text = {"jira:AL-1": "Status: Done. Assignee: Lee.", "jira:AL-2": "Status: In Progress. Assignee: Kim."}.get
    [team] = team_focus(views, docs, text=text, me="Lee", names={"lead@x.com": "Lee"})
    assert [(i["doc_id"], i["assignee"], i["mine"]) for i in team["issues"]["items"]] == [("jira:AL-2", "Kim", False),
                                                                                           ("jira:AL-1", "Lee", True)]
    assert team["project_id"] == "A" and team["leads"] == ["Lee"]
    assert team["issues"]["total"] == 2 and team["issues"]["done"] == 1
    assert team["issues"]["by_status"] == [{"status": "Done", "count": 1}, {"status": "In Progress", "count": 1}]
    assert [u["doc_id"] for u in team["updates"]] == ["jira:AL-2", "jira:AL-1", "slack:C_AL/t1"]      # newest first, ties by id
    assert team["updates"][0]["author"] == "Lee" and team["updates"][0]["place"] == "jira:AL"


def test_registry_places_are_translated_to_the_platforms_ids():
    [alpha] = parse(project(components={"slack": ["C_AL"]}))
    real = {"slack:C_AL": "slack:C0REAL"}
    [team] = team_focus([ProjectView(alpha, ["slack:C_AL"], True)], [row("slack:C0REAL/t1", "slack:C0REAL")],
                        text=lambda _: "", container=real.get)
    assert [u["place"] for u in team["updates"]] == ["slack:C_AL"] and team["places"] == ["slack:C_AL"]


def test_the_sprint_counts_open_issues_and_those_finished_since_it_began():
    [alpha] = parse(project(start_date="2026-09-14", target_date="2026-10-30", milestone={"label": "Cutover", "date": "2026-10-27"},
                            sprint={"name": "Sprint 42", "start_date": "2026-10-05", "end_date": "2026-10-16"}))
    docs = [row("jira:AL-1", "jira:AL", "2026-09-29T10:00:00Z"),      # done before the sprint: not in it
            row("jira:AL-2", "jira:AL", "2026-10-05T10:00:00Z"),      # done on its first day
            row("jira:AL-3", "jira:AL", "2026-09-01T10:00:00Z"),      # old, still open
            row("jira:AL-4", "jira:AL", "2026-10-08T10:00:00Z")]      # no status: not counted
    text = {"jira:AL-1": "Status: Done.", "jira:AL-2": "Status: Done.", "jira:AL-3": "Status: In Progress.", "jira:AL-4": ""}.get
    [team] = team_focus([ProjectView(alpha, ["jira:AL"], True)], docs, text=text)
    assert team["sprint"] == {"name": "Sprint 42", "start_date": "2026-10-05", "end_date": "2026-10-16", "goal": None,
                              "total": 2, "done": 1}
    assert team["start_date"] == "2026-09-14" and team["milestone"] == {"label": "Cutover", "date": "2026-10-27"}
    assert team["issues"]["total"] == 4 and team["issues"]["done"] == 2
    [bare] = team_focus([ProjectView(parse(project())[0], ["jira:AL"], True)], docs, text=text)
    assert bare["sprint"] is None and bare["milestone"] is None and bare["start_date"] is None


# -- the endpoint ----------------------------------------------------------------------------------------------------------
def test_the_endpoint_needs_a_login(client):
    assert client.get("/v1/team").status_code == 401


def test_the_stub_and_the_brain_answer_alike():
    client = TestClient(create_app(fixture_runtime(Settings(floor_latency_ms=0, dev_auth=True))))
    stub = TestClient(stub_app)
    for who in PERSONAS:
        assert client.get("/v1/team", headers=H(who)).json() == stub.get("/v1/team", headers=H(who)).json(), who


def test_a_team_is_a_project_the_caller_is_a_member_of(client):
    assert set(teams(client, "priya")) == {"DBMIG", "PAYINC", "AUTH"}
    assert set(teams(client, "dana")) == {"DBMIG", "PAYINC", "AUTH", "SEC"}
    assert teams(client, "jordan") == {}                       # sees public pages of projects, is part of none
    assert teams(client, "sam") == {}
    dbmig = teams(client, "priya")["DBMIG"]
    assert dbmig["leads"] == ["Maya"] and dbmig["goal"] and dbmig["target_date"] == "2026-10-30"
    assert dbmig["issues"]["total"] == 7 and dbmig["issues"]["done"] == 3
    assert dbmig["issues"]["by_status"] == [{"status": "Done", "count": 3}, {"status": "In Progress", "count": 1},
                                            {"status": "In Review", "count": 1}, {"status": "To Do", "count": 2}]


def test_two_members_of_one_project_see_different_teams(client):
    priya, maya, dana = (teams(client, who)["DBMIG"] for who in ("priya", "maya", "dana"))
    assert PRIVATE in maya["places"] and PRIVATE not in priya["places"]
    assert any(u["place"] == PRIVATE for u in maya["updates"]) and not any(u["place"] == PRIVATE for u in priya["updates"])
    assert dana["issues"] == {"total": 0, "done": 0, "by_status": [], "items": []}      # in the channel, not in the Jira project
    mine = lambda team: {i["doc_id"] for i in team["issues"]["items"] if i["mine"]}      # noqa: E731
    assert "jira:DBMIG-142" in mine(maya) and "jira:DBMIG-142" not in mine(priya) and "jira:DBMIG-150" in mine(priya)
    assert dana["places"] == ["slack:C_DBMIG"]
    assert (priya["sprint"]["done"], priya["sprint"]["total"]) == (1, 5) and priya["sprint"]["name"] == "Sprint 42"
    assert (dana["sprint"]["done"], dana["sprint"]["total"]) == (0, 0)                  # the sprint's name and goal, no numbers


def test_a_revocation_changes_the_team_at_once(rt, client):
    before = teams(client, "priya")["AUTH"]
    assert any(u["place"] == "slack:C_AUTHPRIV" for u in before["updates"])
    rt.advance("e2")                                           # Priya leaves the private auth channel
    after = teams(client, "priya")["AUTH"]
    assert "slack:C_AUTHPRIV" not in after["places"] and not any(u["place"] == "slack:C_AUTHPRIV" for u in after["updates"])

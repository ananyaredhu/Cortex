"""Moving one's own Jira issue (`POST /v1/issues/move`, api.md 0.3) is the Brain's only write to a source, so it gets the
security tests the contract names before it is switched on: only the assignee, a forbidden issue indistinguishable from a
missing one, no change to anyone's access, Jira and the index never disagreeing, and every outcome in the audit log.
"""
import json
from pathlib import Path

from fastapi.testclient import TestClient

from brain.api.app import create_app
from brain.config import Settings
from brain.runtime import fixture_runtime
from connectors.base import TransitionRefused

H = lambda p: {"Authorization": f"Bearer dev:{p}"}   # noqa: E731
PERSONAS = ("priya", "sam", "dana", "jordan", "maya")
MINE = "jira:PAYINC-10"          # Priya's, In Progress
MINE_TODO = "jira:DBMIG-150"     # Priya's, To Do
MAYAS = "jira:DBMIG-142"         # Priya can open it; Maya is the assignee
HIDDEN = "jira:SEC-17"           # Dana's; Priya and Sam cannot open it
MISSING = "jira:SEC-999"


def _target():
    runtime = fixture_runtime(Settings(floor_latency_ms=0, dev_auth=True))
    return TestClient(create_app(runtime)), runtime


def _move(client, who, doc_id, status):
    return client.post("/v1/issues/move", json={"doc_id": doc_id, "status": status}, headers=H(who))


def _issue(client, who, doc_id) -> dict | None:
    teams = client.get("/v1/team", headers=H(who)).json()["teams"]
    return next((i for t in teams for i in t["issues"]["items"] if i["doc_id"] == doc_id), None)


def _transitions(runtime) -> list[dict]:
    return [e for e in runtime.audit_store.all() if e["event_type"] == "issue_transition"]


def test_the_assignee_moves_their_issue_and_jira_the_index_and_the_next_sync_agree():
    client, runtime = _target()
    jira = runtime.brain.connectors["jira"]
    before = jira.version(MINE)
    assert _issue(client, "priya", MINE)["can_move"] is True

    moved = _move(client, "priya", MINE, "done")           # any case: Jira's own name comes back
    assert moved.status_code == 200
    assert moved.json() | {"request_id": "", "updated_at": ""} == {
        "request_id": "", "doc_id": MINE, "status": "Done", "updated_at": "", "indexed": True}

    # Jira holds the new status, and the index already says the same, for the mover and for a colleague
    assert jira.fetch(MINE).fields["status"] == "Done" and jira.version(MINE) != before
    assert _issue(client, "priya", MINE)["status"] == "Done"
    assert _issue(client, "maya", MINE)["status"] == "Done"
    assert runtime.brain.index.document(MINE).version == jira.version(MINE)

    # the next sync meets the version it already has: nothing is rewritten and nothing snaps back
    runtime.ingest()
    assert runtime.brain.index.document(MINE).version == jira.version(MINE)
    assert _issue(client, "priya", MINE)["status"] == "Done"


def test_someone_who_can_open_an_issue_but_is_not_its_assignee_cannot_move_it():
    client, runtime = _target()
    jira = runtime.brain.connectors["jira"]
    before = jira.version(MAYAS)
    assert _issue(client, "priya", MAYAS)["can_move"] is False

    refused = _move(client, "priya", MAYAS, "Done")
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "not_assignee"
    assert jira.version(MAYAS) == before and jira.fetch(MAYAS).fields["status"] == "In Progress"
    assert _move(client, "maya", MAYAS, "Done").status_code == 200      # its assignee can


def test_a_forbidden_issue_and_a_missing_one_are_indistinguishable():
    client, runtime = _target()
    jira = runtime.brain.connectors["jira"]
    before = jira.version(HIDDEN)
    for who in ("priya", "sam"):
        forbidden, missing = _move(client, who, HIDDEN, "Done"), _move(client, who, MISSING, "Done")
        other_source = _move(client, who, "confluence:SEC/q3-breach-report", "Done")
        assert forbidden.status_code == missing.status_code == other_source.status_code == 404
        assert forbidden.json() == missing.json() == other_source.json() == {"detail": "no such issue"}
    assert jira.version(HIDDEN) == before

    # the log records the attempts without naming the issue, and without the block that would say where it stood
    events = _transitions(runtime)
    assert len(events) == 6
    for event in events:
        assert "transition" not in event
        assert event["decisions"][0]["allowed"] is False and "doc_id" not in event["decisions"][0]
        assert "SEC-17" not in json.dumps(event) and "In Progress" not in json.dumps(event)


def test_access_lost_between_page_load_and_drop_answers_like_a_missing_issue():
    client, runtime = _target()
    jira = runtime.brain.connectors["jira"]
    assert _issue(client, "priya", MINE)["can_move"] is True
    jira.state.persona_tokens["priya"] -= {"role:PAYINC:developer"}       # removed from the project meanwhile
    runtime.brain.resolver.invalidate("priya@companya.com")
    runtime.brain.pdp.invalidate_user("priya@companya.com")

    gone = _move(client, "priya", MINE, "Done")
    assert gone.status_code == 404 and gone.json() == {"detail": "no such issue"}
    assert jira.fetch(MINE).fields["status"] == "In Progress"


def test_jira_decides_which_moves_exist():
    client, runtime = _target()
    jira = runtime.brain.connectors["jira"]
    before = jira.version(MINE_TODO)
    refused = _move(client, "priya", MINE_TODO, "Done")                   # the workflow goes through In Progress
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "transition_refused"
    assert jira.version(MINE_TODO) == before

    same = _move(client, "priya", MINE_TODO, "To Do")                     # already there: nothing to do, nothing changed
    assert same.status_code == 200 and same.json()["status"] == "To Do" and jira.version(MINE_TODO) == before
    assert _move(client, "priya", MINE_TODO, "In Progress").status_code == 200

    for bad in ("", "   ", "x" * 41, "Done\nand more"):
        assert _move(client, "priya", MINE, bad).status_code == 422
    assert [e["transition"]["outcome"] for e in _transitions(runtime)] == ["refused_by_source", "unchanged", "moved"]


def test_an_unreachable_jira_changes_nothing_and_is_not_retried(monkeypatch):
    client, runtime = _target()
    jira = runtime.brain.connectors["jira"]
    calls = []

    def down(identity, doc_id, status):
        calls.append(doc_id)
        raise TimeoutError

    monkeypatch.setattr(jira, "transition", down)
    failed = _move(client, "priya", MINE, "Done")
    assert failed.status_code == 503 and failed.json()["error"]["code"] == "source_unavailable"
    assert calls == [MINE]
    assert _issue(client, "priya", MINE)["status"] == "In Progress"
    assert _transitions(runtime)[-1]["transition"]["outcome"] == "source_unavailable"

    monkeypatch.setattr(jira, "transition", lambda *a: (_ for _ in ()).throw(TransitionRefused(MINE)))
    assert _move(client, "priya", MINE, "Done").status_code == 409


def test_a_move_changes_what_no_one_may_see():
    client, runtime = _target()
    jira = runtime.brain.connectors["jira"]

    def view():
        return {who: sorted(d["doc_id"] for d in client.get("/v1/mywork", headers=H(who)).json()["issues"]) for who in PERSONAS}

    seen, tokens = view(), jira.fetch(MINE).acl.tokens
    assert _move(client, "priya", MINE, "Done").status_code == 200
    assert view() == seen and jira.fetch(MINE).acl.tokens == tokens
    assert _issue(client, "sam", MINE) is None             # still nothing for someone outside the project


def test_every_outcome_is_logged_with_the_caller_as_actor_and_the_chain_verifies():
    client, runtime = _target()
    _move(client, "priya", MINE, "Done")
    _move(client, "priya", MAYAS, "Done")
    _move(client, "sam", HIDDEN, "Done")
    events = _transitions(runtime)
    assert [e["actor"]["user_id"] for e in events] == ["priya@companya.com", "priya@companya.com", "sam@contractor.io"]
    moved, refused, denied = events
    assert moved["transition"] | {"doc_version_after": ""} == {
        "from": "In Progress", "to": "Done", "outcome": "moved", "indexed": True, "doc_version_after": ""}
    assert moved["decisions"][0]["doc_id"] == MINE and moved["decisions"][0]["allowed"] is True
    assert refused["transition"] == {"from": "In Progress", "to": "Done", "outcome": "refused_not_assignee"}
    assert "transition" not in denied
    assert client.get("/v1/audit/verify", headers=H("jordan")).json()["ok"] is True


def test_the_move_is_not_reachable_from_the_llm_plane():
    """AGENTS rule 3: no write tools. The MCP server and the skills must not know the endpoint or the method."""
    root = Path(__file__).resolve().parents[2]
    llm_plane = [*(root / "mcp_server").rglob("*.py"), *(root / "brain" / "skills").rglob("*"), *(root / "brain" / "gateway").rglob("*.py")]
    for path in llm_plane:
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert "move_issue" not in text and "issues/move" not in text and ".transition(" not in text, path

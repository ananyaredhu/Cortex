"""The simulator's HTTP API and the connector on top of it, on the Company A seed."""
import httpx
import pytest
from fastapi.testclient import TestClient

from connectors.base import PlatformIdentity
from simulators.common import DocumentNotFound
from simulators.jira.app import create_app
from simulators.jira.connector import JiraConnector
from simulators.jira.model import JiraSim
from simulators.jira.seed import seed_company_a
from simulators.jira.testing import SeededJira

API = "/rest/api/2"
PRIYA, SAM, DANA, MAYA = "priya@companya.com", "sam@contractor.io", "dana@companya.com", "maya@companya.com"
AS = lambda email: {"X-Sim-User": email}  # noqa: E731


@pytest.fixture
def conn() -> SeededJira:
    return SeededJira()


# ---------------------------------------------------------------------------------- read API
def test_issue_has_jira_shape(conn):
    issue = conn.http.get(f"{API}/issue/DBMIG-142").json()
    fields = issue["fields"]
    assert issue["key"] == "DBMIG-142" and fields["project"]["key"] == "DBMIG"
    assert fields["issuetype"] == {"name": "Task", "subtask": False}
    assert fields["reporter"]["emailAddress"] == MAYA and fields["security"] is None
    assert fields["issuelinks"] == [{"type": {"name": "Relates"}, "outwardIssue": {"key": "DBMIG-150"}}]
    assert conn.http.get(f"{API}/issue/SEC-17").json()["fields"]["security"] == {"name": "security"}


def test_forbidden_and_missing_issues_are_the_same_404(conn):
    forbidden = conn.http.get(f"{API}/issue/SEC-17", headers=AS(PRIYA))
    missing = conn.http.get(f"{API}/issue/SEC-999", headers=AS(PRIYA))
    assert forbidden.status_code == missing.status_code == 404
    assert forbidden.json() == missing.json(), "the 404 must not differ, or name the issue"
    assert conn.http.get(f"{API}/issue/SEC-17", headers=AS(DANA)).status_code == 200


def test_search_shows_a_user_only_what_they_may_see(conn):
    keys = lambda headers, **params: {i["key"] for i in conn.http.get(f"{API}/search", headers=headers, params=params).json()["issues"]}  # noqa: E731
    assert keys({}) == set(conn.sim.issues), "no user = service account"
    assert keys(AS(DANA)) == {"SEC-17"}
    assert keys(AS(PRIYA)) == set(conn.sim.issues) - {"SEC-17"}
    assert keys(AS(SAM)) == keys(AS("ghost@nowhere.example")) == set()
    assert keys(AS(PRIYA), jql="project = PAYINC") == {"PAYINC-9", "PAYINC-10", "PAYINC-11"}
    assert conn.http.get(f"{API}/search", params={"jql": "assignee = currentUser()"}).status_code == 400
    assert conn.http.get(f"{API}/search", headers=AS(PRIYA), params={"jql": "project = SEC"}).json()["total"] == 0


def test_roles_and_security_levels_are_readable(conn):
    role = conn.http.get(f"{API}/project/DBMIG/role/lead").json()
    assert [a["displayName"] for a in role["actors"]] == [MAYA]
    levels = conn.http.get(f"{API}/project/SEC/securitylevel").json()["levels"]
    assert [(lv["name"], lv["_simulator"]["members"]["groups"]) for lv in levels] == [("security", ["security-team"])]
    assert conn.http.get(f"{API}/project/NOPE").status_code == 404


def test_permission_check_endpoint(conn):
    dana, priya = (conn.resolve_identity(e).platform_user_id for e in (DANA, PRIYA))
    check = lambda account, keys: conn.http.post(f"{API}/permissions/check", json={  # noqa: E731
        "accountId": account, "projectPermissions": [{"permissions": ["BROWSE_PROJECTS"], "issues": keys}]}).json()
    mixed = ["SEC-17", "DBMIG-142", "SEC-999"]
    assert check(dana, mixed)["projectPermissions"][0]["issues"] == ["SEC-17"]
    assert check(priya, mixed)["projectPermissions"][0]["issues"] == ["DBMIG-142"]
    assert check("no-such-account", mixed)["projectPermissions"][0]["issues"] == []
    bad = conn.http.post(f"{API}/permissions/check", json={
        "accountId": dana, "projectPermissions": [{"permissions": ["ADMINISTER_PROJECTS"], "issues": ["SEC-17"]}]})
    assert bad.status_code == 400


# ---------------------------------------------------------------------------------- admin API
def test_hidden_subtask_inherits_security_level_over_http(conn):
    """Hidden-document flow used by Leak-CI: plant a sub-task under a security-level issue, then remove it."""
    conn.sim.set_role_actors("SEC", "developer", users=[PRIYA, DANA])
    conn.sim.set_browse("SEC", roles=["developer"], groups=["security-team"])     # Priya can now browse SEC
    body = {"project": "SEC", "summary": "Rotate replay keys", "description": "CANARY-test", "parent_key": "SEC-17"}
    created = conn.http.post("/sim/admin/issues", json=body).json()
    doc_id = f"jira:{created['key']}"
    assert created["fields"]["issuetype"]["subtask"] is True and created["fields"]["parent"] == {"key": "SEC-17"}
    priya, dana = conn.resolve_identity(PRIYA), conn.resolve_identity(DANA)
    assert conn.check_access(dana, doc_id).allowed
    assert not conn.check_access(priya, doc_id).allowed, "Browse is not enough: the parent's level applies"
    assert conn.fetch(doc_id).acl.tokens == ["group:jira:security-team"]
    cursor = conn.list_changes(None).next_cursor
    assert conn.http.delete(f"/sim/admin/issues/{created['key']}").status_code == 200
    assert [(c.type, c.doc_id) for c in conn.list_changes(cursor).changes] == [("delete", doc_id)]
    with pytest.raises(DocumentNotFound):
        conn.fetch(doc_id)


def test_set_security_level_then_revoke_role_over_http(conn):
    doc_id = "jira:PAYINC-9"
    priya = conn.resolve_identity(PRIYA)
    assert conn.check_access(priya, doc_id).proof_path == [f"user:{PRIYA}", "role:PAYINC:developer"]
    conn.http.put("/sim/admin/projects/PAYINC/securitylevels/leads-only", json={"users": [MAYA]}).raise_for_status()
    conn.http.put("/sim/admin/issues/PAYINC-9/security", json={"level": "leads-only"}).raise_for_status()
    assert not conn.check_access(priya, doc_id).allowed
    assert conn.check_access(conn.resolve_identity(MAYA), doc_id).allowed
    assert conn.fetch(doc_id).acl.tokens == [f"user:{MAYA}"]
    conn.http.put("/sim/admin/issues/PAYINC-9/security", json={"level": None}).raise_for_status()
    assert conn.check_access(priya, doc_id).allowed
    assert conn.http.delete(f"/sim/admin/projects/PAYINC/roles/developer/users/{PRIYA}").json()["changed"] is True
    assert not conn.check_access(priya, doc_id).allowed, "a stale identity object must not keep access"
    assert "role:PAYINC:developer" not in conn.resolve_identity(PRIYA).groups


def test_status_and_comments_become_part_of_the_document(conn):
    before = conn.version("jira:PAYINC-10")
    cursor = conn.list_changes(None).next_cursor
    conn.http.put("/sim/admin/issues/PAYINC-10", json={"status": "Done"}).raise_for_status()
    conn.http.post("/sim/admin/issues/PAYINC-10/comments", json={"body": "Circuit breaker shipped.", "author": PRIYA})
    doc = conn.fetch("jira:PAYINC-10")
    assert doc.version != before
    assert "Status: Done" in doc.body and f"Comment by {PRIYA}" in doc.body and "Circuit breaker shipped." in doc.body
    assert {(c.type, c.doc_id) for c in conn.list_changes(cursor).changes} == {("upsert", "jira:PAYINC-10")}


def test_admin_errors_and_reset(conn):
    assert conn.http.put("/sim/admin/issues/NOPE-1", json={"summary": "x"}).status_code == 404
    assert conn.http.post("/sim/admin/issues", json={"project": "SEC", "summary": "dup", "key": "SEC-17"}).status_code == 400
    assert conn.http.put("/sim/admin/issues/SEC-17/security", json={"level": "nope"}).status_code == 400
    assert conn.http.get("/sim/changes", params={"cursor": "junk"}).status_code == 400
    conn.http.delete("/sim/admin/issues/SEC-17")
    assert conn.http.post("/sim/admin/reset").json() == {"seed": "company_a", "issues": 6}
    assert conn.http.get(f"{API}/issue/SEC-17").status_code == 200
    assert conn.http.post("/sim/admin/reset", json={"seed": "empty"}).json()["issues"] == 0


def test_admin_token_is_enforced_when_configured():
    sim = JiraSim()
    seed_company_a(sim)
    http = TestClient(create_app(sim, admin_token="s3cret"))
    assert http.put("/sim/admin/issues/SEC-17", json={"summary": "x"}).status_code == 401
    assert http.post("/sim/webhooks", json={"url": "http://example.invalid/hook"}).status_code == 401
    ok = http.put("/sim/admin/issues/SEC-17", json={"summary": "x"}, headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200
    assert http.get(f"{API}/issue/SEC-17").status_code == 200, "the read API needs no admin token"


def test_webhooks_fire_for_each_change(conn):
    sent: list[tuple[list[str], list[dict]]] = []
    conn.http.app.state.deliver = lambda urls, payloads: sent.append((urls, payloads))
    assert conn.http.post("/sim/webhooks", json={"url": "ftp://nope"}).status_code == 400
    conn.http.post("/sim/webhooks", json={"url": "http://brain.invalid/hooks/jira"})
    conn.http.put("/sim/admin/issues/DBMIG-150", json={"status": "In Progress"})
    conn.http.delete(f"/sim/admin/groups/security-team/members/{DANA}")
    events = [(p["webhookEvent"], p["_simulator"]["doc_id"]) for _, payloads in sent for p in payloads]
    assert events == [("jira:issue_updated", "jira:DBMIG-150"), ("group_membership_updated", "jira:SEC-17")]
    assert all(urls == ["http://brain.invalid/hooks/jira"] for urls, _ in sent)


# ---------------------------------------------------------------------------------- connector
def test_malformed_doc_ids_are_nonexistent(conn):
    dana = conn.resolve_identity(DANA)
    assert conn.check_access(dana, "jira:SEC-17").allowed
    for bad in ("confluence:SEC-17", "jira:SEC-17/../x", "jira:sec-17", "jira:SEC-0", "jira:", "garbage"):
        assert conn.check_access(dana, bad).allowed is False, bad
        with pytest.raises(DocumentNotFound):
            conn.fetch(bad)
        with pytest.raises(DocumentNotFound):
            conn.version(bad)


def test_identity_from_another_source_is_denied(conn):
    dana = conn.resolve_identity(DANA)
    foreign = PlatformIdentity("confluence", dana.platform_user_id, dana.email, dana.groups)
    assert conn.check_access(foreign, "jira:SEC-17").allowed is False


def test_connector_fails_closed_when_the_simulator_is_unreachable_or_broken():
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulator is down", request=request)

    def garbage(request: httpx.Request) -> httpx.Response:
        # Claims the issue is granted but carries no grant path: must not be believed.
        return httpx.Response(200, json={"projectPermissions": [{"issues": ["SEC-17"]}], "_simulator": {"issues": {"SEC-17": {}}}})

    def wrong_shape(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["unexpected"])

    def server_error(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    dana = PlatformIdentity("jira", "jira-anything", DANA, ["group:jira:security-team"])
    for handler in (down, garbage, wrong_shape, server_error):
        connector = JiraConnector(httpx.Client(base_url="http://sim.invalid", transport=httpx.MockTransport(handler)))
        decision = connector.check_access(dana, "jira:SEC-17")
        assert (decision.allowed, decision.proof_path) == (False, []), handler.__name__
        assert connector.resolve_identity(DANA) is None, handler.__name__

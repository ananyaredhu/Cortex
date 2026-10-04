"""The simulator's HTTP API and the connector on top of it, on the Company A seed."""
import httpx
import pytest
from fastapi.testclient import TestClient

from connectors.base import PlatformIdentity
from simulators.confluence.app import create_app
from simulators.confluence.connector import ConfluenceConnector, DocumentNotFound
from simulators.confluence.model import ConfluenceSim
from simulators.confluence.seed import seed_company_a
from simulators.confluence.testing import SeededConfluence

API = "/wiki/rest/api"
BREACH = "q3-breach-report"
AS = lambda email: {"X-Sim-User": email}  # noqa: E731


@pytest.fixture
def conn() -> SeededConfluence:
    return SeededConfluence()


# ---------------------------------------------------------------------------------- read API
def test_page_has_confluence_shape(conn):
    page = conn.http.get(f"{API}/content/runbook-payment-service").json()
    assert page["type"] == "page" and page["space"]["key"] == "PAY"
    assert page["body"]["storage"]["representation"] == "storage"
    assert page["version"]["number"] == 1 and page["ancestors"] == []
    assert page["_links"]["webui"] == "/wiki/spaces/PAY/pages/runbook-payment-service"


def test_forbidden_and_missing_pages_are_the_same_404(conn):
    sam = AS("sam@contractor.io")
    forbidden = conn.http.get(f"{API}/content/{BREACH}", headers=sam)
    missing = conn.http.get(f"{API}/content/does-not-exist", headers=sam)
    assert forbidden.status_code == missing.status_code == 404
    assert forbidden.json().keys() == missing.json().keys()
    assert "breach" not in forbidden.text.replace(BREACH, "").lower(), "the 404 must not describe the page"
    for path in ("restriction/byOperation/read", "child/page"):
        assert conn.http.get(f"{API}/content/{BREACH}/{path}", headers=sam).status_code == 404
    assert conn.http.get(f"{API}/content/{BREACH}", headers=AS("dana@companya.com")).status_code == 200


def test_listing_shows_a_user_only_what_they_may_read(conn):
    ids = lambda headers: {p["id"] for p in conn.http.get(f"{API}/content", headers=headers).json()["results"]}  # noqa: E731
    assert ids({}) == set(conn.sim.pages), "no user = service account"
    assert ids(AS("sam@contractor.io")) == {"contractor-onboarding"}
    assert BREACH not in ids(AS("priya@companya.com")) and BREACH in ids(AS("dana@companya.com"))
    assert ids(AS("ghost@nowhere.example")) == set()


def test_space_permissions_and_restrictions_are_readable(conn):
    space = conn.http.get(f"{API}/space/SEC").json()
    assert [g["name"] for g in space["permissions"][0]["subjects"]["group"]["results"]] == ["security-team"]
    restriction = conn.http.get(f"{API}/content/{BREACH}/restriction/byOperation/read").json()
    assert [g["name"] for g in restriction["restrictions"]["group"]["results"]] == ["security-team"]
    assert conn.http.get(f"{API}/space/NOPE").status_code == 404


def test_permission_check_endpoint(conn):
    dana, sam = (conn.resolve_identity(e).platform_user_id for e in ("dana@companya.com", "sam@contractor.io"))
    check = lambda account, page: conn.http.post(  # noqa: E731
        f"{API}/content/{page}/permission/check", json={"subject": {"type": "user", "identifier": account}, "operation": "read"})
    assert check(dana, BREACH).json()["hasPermission"] is True
    assert check(sam, BREACH).json()["hasPermission"] is False
    assert check(sam, "does-not-exist").json()["hasPermission"] is False
    assert check("no-such-account", BREACH).json()["hasPermission"] is False
    bad = conn.http.post(f"{API}/content/{BREACH}/permission/check", json={"subject": {"type": "group", "identifier": "x"}})
    assert bad.status_code == 400


# ---------------------------------------------------------------------------------- admin API
def test_nested_page_inherits_restriction_over_http(conn):
    """Hidden-document flow used by Leak-CI: plant a page under a restricted parent, then remove it."""
    page = {"id": "breach-appendix", "space": "SEC", "title": "Appendix", "body": "CANARY-test", "parent_id": BREACH}
    conn.sim.set_space_view("SEC", groups=["security-team", "confluence-users"])   # open the space; only the page restriction is left
    assert conn.http.post("/sim/admin/pages", json=page).status_code == 200
    doc_id = "confluence:SEC/breach-appendix"
    priya, dana = (conn.resolve_identity(e) for e in ("priya@companya.com", "dana@companya.com"))
    assert conn.check_access(dana, doc_id).allowed
    assert not conn.check_access(priya, doc_id).allowed, "inherits the parent's restriction"
    assert conn.fetch(doc_id).acl.tokens == ["group:confluence:security-team"]
    assert conn.http.get(f"{API}/content/breach-appendix").json()["ancestors"][0]["id"] == BREACH
    cursor = conn.list_changes(None).next_cursor
    assert conn.http.delete("/sim/admin/pages/breach-appendix").status_code == 200
    assert [(c.type, c.doc_id) for c in conn.list_changes(cursor).changes] == [("delete", doc_id)]
    with pytest.raises(DocumentNotFound):
        conn.fetch(doc_id)


def test_restrict_then_revoke_over_http(conn):
    doc_id = "confluence:PAY/runbook-payment-service"
    priya = conn.resolve_identity("priya@companya.com")
    assert conn.check_access(priya, doc_id).allowed
    body = {"groups": ["payments-eng"], "users": []}
    assert conn.http.put("/sim/admin/pages/runbook-payment-service/restrictions", json=body).status_code == 200
    assert conn.check_access(priya, doc_id).proof_path == ["user:priya@companya.com", "public:org", "group:confluence:payments-eng"]
    assert not conn.check_access(conn.resolve_identity("maya@companya.com"), doc_id).allowed
    assert conn.http.delete("/sim/admin/groups/payments-eng/members/priya@companya.com").json()["changed"] is True
    assert not conn.check_access(priya, doc_id).allowed, "a stale identity object must not keep access"
    assert "group:confluence:payments-eng" not in conn.resolve_identity("priya@companya.com").groups


def test_admin_errors_and_reset(conn):
    assert conn.http.put("/sim/admin/pages/nope", json={"body": "x"}).status_code == 404
    assert conn.http.post("/sim/admin/pages", json={"id": BREACH, "space": "SEC", "title": "dup"}).status_code == 400
    assert conn.http.get("/sim/changes", params={"cursor": "junk"}).status_code == 400
    conn.http.delete(f"/sim/admin/pages/{BREACH}")
    assert conn.http.post("/sim/admin/reset").json() == {"seed": "company_a", "pages": 4}
    assert conn.http.get(f"{API}/content/{BREACH}").status_code == 200
    assert conn.http.post("/sim/admin/reset", json={"seed": "empty"}).json()["pages"] == 0


def test_admin_token_is_enforced_when_configured():
    sim = ConfluenceSim()
    seed_company_a(sim)
    http = TestClient(create_app(sim, admin_token="s3cret"))
    assert http.put("/sim/admin/pages/runbook-payment-service", json={"body": "x"}).status_code == 401
    assert http.post("/sim/webhooks", json={"url": "http://example.invalid/hook"}).status_code == 401
    ok = http.put("/sim/admin/pages/runbook-payment-service", json={"body": "x"}, headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200
    assert http.get(f"{API}/content/runbook-payment-service").status_code == 200, "the read API needs no admin token"


def test_webhooks_fire_for_each_change(conn):
    sent: list[tuple[list[str], list[dict]]] = []
    conn.http.app.state.deliver = lambda urls, payloads: sent.append((urls, payloads))
    assert conn.http.post("/sim/webhooks", json={"url": "ftp://nope"}).status_code == 400
    conn.http.post("/sim/webhooks", json={"url": "http://brain.invalid/hooks/confluence"})
    conn.http.put("/sim/admin/pages/runbook-payment-service", json={"body": "edited"})
    conn.http.put(f"/sim/admin/pages/{BREACH}/restrictions", json={"groups": [], "users": ["dana@companya.com"]})
    assert [urls for urls, _ in sent] == [["http://brain.invalid/hooks/confluence"]] * 2
    events = [(p["webhookEvent"], p["_simulator"]["doc_id"]) for _, payloads in sent for p in payloads]
    assert events == [("page_updated", "confluence:PAY/runbook-payment-service"),
                      ("content_permissions_updated", f"confluence:SEC/{BREACH}")]


# ---------------------------------------------------------------------------------- connector
def test_doc_id_must_name_the_right_space(conn):
    dana = conn.resolve_identity("dana@companya.com")
    assert conn.check_access(dana, f"confluence:SEC/{BREACH}").allowed
    for bad in (f"confluence:PAY/{BREACH}", f"jira:SEC/{BREACH}", "confluence:SEC/../x", "confluence:", "garbage"):
        assert conn.check_access(dana, bad).allowed is False, bad
        with pytest.raises(DocumentNotFound):
            conn.fetch(bad)
        with pytest.raises(DocumentNotFound):
            conn.version(bad)


def test_identity_from_another_source_is_denied(conn):
    dana = conn.resolve_identity("dana@companya.com")
    foreign = PlatformIdentity("jira", dana.platform_user_id, dana.email, dana.groups)
    assert conn.check_access(foreign, f"confluence:SEC/{BREACH}").allowed is False


def test_connector_fails_closed_when_the_simulator_is_unreachable_or_broken():
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulator is down", request=request)

    def garbage(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"hasPermission": "yes"})   # not `true`, and no grant path

    def wrong_shape(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["unexpected"])

    def server_error(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    dana = PlatformIdentity("confluence", "cf-anything", "dana@companya.com", ["group:confluence:security-team"])
    for handler in (down, garbage, wrong_shape, server_error):
        connector = ConfluenceConnector(httpx.Client(base_url="http://sim.invalid", transport=httpx.MockTransport(handler)))
        decision = connector.check_access(dana, f"confluence:SEC/{BREACH}")
        assert (decision.allowed, decision.proof_path) == (False, []), handler.__name__
        assert connector.resolve_identity("dana@companya.com") is None, handler.__name__

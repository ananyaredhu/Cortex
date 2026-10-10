"""The MCP server: another front door into the same Brain. Spoken to the way a real client does, JSON-RPC over HTTP."""
import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from brain.api.app import create_app
from brain.auth import mint_token
from brain.config import Settings
from brain.runtime import fixture_runtime

KEY = "m" * 32
Q1 = "What's the status of the database migration, and were there blockers raised in Slack last week?"
BREACH = "Show me the security incident report from the Q3 breach"
NONEXISTENT = "Show me the Q9 quantum hologram audit report"
H = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}

# Pinned: tool names, descriptions and input schemas. If this fails you changed what a client's model reads. Update
# docs/02-contracts/mcp-tools.md in the same PR (all three reviewers) and then this value.
PINNED_FINGERPRINT = "18f8d10f33cf06cc2b5153ee53acc9fd5bf4c81a85dffcfd9406a3f3e844dc5f"


def make(**overrides):
    settings = Settings(floor_latency_ms=0, jwt_signing_key=KEY, mock_idp=True, dev_auth=False, mcp=True,
                        mcp_allowed_hosts=("testserver",), **overrides)
    runtime = fixture_runtime(settings)
    return runtime, TestClient(create_app(runtime))


@pytest.fixture
def rt_client():
    runtime, client = make()
    with client:                                    # runs the app's lifespan, which starts the MCP session manager
        yield runtime, client


def token(client, persona):
    return client.post("/idp/token", json={"persona": persona}).json()["access_token"]


def rpc(client, method, params=None, who=None, bearer=None, id_=1):
    headers = dict(H)
    jwt = bearer if bearer is not None else (token(client, who) if who else None)
    if jwt:
        headers["Authorization"] = f"Bearer {jwt}"
    return client.post("/mcp", headers=headers, content=json.dumps({"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}))


def call(client, who, tool, **arguments):
    """The tool's structured result as a dict, plus the isError flag."""
    body = rpc(client, "tools/call", {"name": tool, "arguments": arguments}, who=who).json()["result"]
    data = body.get("structuredContent")
    if isinstance(data, dict) and set(data) == {"result"}:
        data = data["result"]
    return data, body["isError"]


def strip(r):
    return {k: v for k, v in r.items() if k not in ("request_id", "conversation_id")}


# -- off by default, and authenticated on every request -----------------------------------------------------------
def test_mcp_is_not_served_unless_enabled():
    client = TestClient(create_app(fixture_runtime(Settings(floor_latency_ms=0, jwt_signing_key=KEY, dev_auth=False))))
    assert client.post("/mcp", headers=H, content="{}").status_code in (404, 405)


def test_every_request_needs_a_valid_token(rt_client):
    _, client = rt_client
    missing = rpc(client, "tools/list")
    assert missing.status_code == 401 and "Bearer" in missing.headers["www-authenticate"]
    assert rpc(client, "tools/list", bearer="not-a-token").status_code == 401
    expired = mint_token(KEY, "internal-brain", "priya@companya.com", ["engineer"], ttl_s=-5)
    assert rpc(client, "tools/list", bearer=expired).status_code == 401
    wrong_key = mint_token("w" * 32, "internal-brain", "priya@companya.com", ["engineer"])
    assert rpc(client, "tools/list", bearer=wrong_key).status_code == 401
    wrong_audience = mint_token(KEY, "someone-else", "priya@companya.com", ["engineer"])
    assert rpc(client, "tools/list", bearer=wrong_audience).status_code == 401


def test_dev_tokens_do_not_work_when_dev_login_is_off(rt_client):
    _, client = rt_client
    assert rpc(client, "tools/list", bearer="dev:priya").status_code == 401


def test_dev_tokens_work_only_when_dev_login_is_on():
    _, client = make(**{})                                               # dev_auth=False above, so build one with it on
    settings = Settings(floor_latency_ms=0, jwt_signing_key=KEY, dev_auth=True, mcp=True, mcp_allowed_hosts=("testserver",))
    with TestClient(create_app(fixture_runtime(settings))) as dev:
        assert rpc(dev, "tools/list", bearer="dev:priya").status_code == 200


def test_a_host_that_is_not_allowed_is_refused():
    runtime, _ = make()
    settings = Settings(floor_latency_ms=0, jwt_signing_key=KEY, mock_idp=True, dev_auth=False, mcp=True,
                        mcp_allowed_hosts=("only.example",))
    with TestClient(create_app(fixture_runtime(settings))) as client:
        jwt = mint_token(KEY, "internal-brain", "priya@companya.com", ["engineer"])
        assert rpc(client, "tools/list", bearer=jwt).status_code == 421


# -- the protocol and the pinned tools ----------------------------------------------------------------------------
def test_initialize_and_list_the_five_tools(rt_client):
    _, client = rt_client
    hello = {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}
    init = rpc(client, "initialize", hello, who="priya")
    assert init.status_code == 200 and init.json()["result"]["serverInfo"]["name"] == "internal-brain"
    tools = rpc(client, "tools/list", who="priya").json()["result"]["tools"]
    assert sorted(t["name"] for t in tools) == ["ask", "audit_query", "explain_access", "get_source", "search"]


def test_tool_descriptions_and_schemas_are_pinned(rt_client):
    from mcp_server.server import TOOL_DESCRIPTIONS
    _, client = rt_client
    tools = {t["name"]: t for t in rpc(client, "tools/list", who="priya").json()["result"]["tools"]}
    for name, text in TOOL_DESCRIPTIONS.items():
        assert tools[name]["description"] == text
    shape = {n: {"description": t["description"], "inputSchema": t["inputSchema"]} for n, t in sorted(tools.items())}
    fingerprint = hashlib.sha256(json.dumps(shape, sort_keys=True).encode()).hexdigest()
    assert fingerprint == PINNED_FINGERPRINT, f"tool surface changed; fingerprint is now {fingerprint}"


def test_no_tool_takes_a_user_argument(rt_client):
    """Identity comes from the token only. A client cannot ask 'as someone else'."""
    _, client = rt_client
    for t in rpc(client, "tools/list", who="priya").json()["result"]["tools"]:
        assert not {"user", "email", "as_user", "principal", "persona"} & set(t["inputSchema"].get("properties", {})), t["name"]


# -- the tools: the same answers and refusals as the UI -----------------------------------------------------------
def test_ask_gives_the_same_answer_as_the_http_api(rt_client):
    runtime, client = rt_client
    via_mcp, is_error = call(client, "priya", "ask", question=Q1)
    via_api = client.post("/v1/ask", json={"question": Q1}, headers={"Authorization": f"Bearer {token(client, 'priya')}"}).json()
    assert not is_error and strip(via_mcp) == strip(via_api)
    assert {c["doc_id"] for c in via_mcp["citations"]} >= {"jira:DBMIG-142", "slack:C_DBMIG/thread-1"}
    asks = [e for e in runtime.audit_store.all() if e["event_type"] == "ask"]
    assert asks[0]["actor"]["client"] == "mcp"                           # the pipeline logged it as an MCP call


def test_a_forbidden_and_a_nonexistent_report_are_refused_identically(rt_client):
    _, client = rt_client
    a, _ = call(client, "sam", "ask", question=BREACH)
    b, _ = call(client, "sam", "ask", question=NONEXISTENT)
    assert a["refused"] and b["refused"] and strip(a) == strip(b)
    assert a["citations"] == [] and "breach" not in json.dumps(a).lower()


def test_search_returns_only_what_the_caller_may_open(rt_client):
    _, client = rt_client
    found, _ = call(client, "priya", "search", query="database migration blockers replica lag")
    ids = [h["doc_id"] for h in found["results"]]
    assert "jira:DBMIG-142" in ids and "slack:C_DBMIGPRIV/thread-1" not in ids
    assert call(client, "sam", "search", query="security incident report Q3 breach")[0] == {"results": []}
    only, _ = call(client, "priya", "search", query="database migration", sources=["jira"], limit=1)
    assert len(only["results"]) == 1 and only["results"][0]["source"] == "jira"


def test_get_source_and_explain_access_do_not_confirm_what_they_cannot_show(rt_client):
    _, client = rt_client
    doc, _ = call(client, "dana", "get_source", doc_id="confluence:SEC/q3-breach-report")
    assert doc["found"] is True and "CANARY" in doc["text"]
    assert call(client, "sam", "get_source", doc_id="confluence:SEC/q3-breach-report")[0] == {"found": False}
    assert call(client, "sam", "get_source", doc_id="confluence:SEC/missing")[0] == {"found": False}
    assert call(client, "sam", "explain_access", doc_id="confluence:SEC/q3-breach-report")[0] == {"found": False}
    why, _ = call(client, "priya", "explain_access", doc_id="jira:DBMIG-142")
    assert why["found"] is True and why["proof_path"][0] == "user:priya@companya.com"


def test_audit_query_is_for_compliance_only(rt_client):
    _, client = rt_client
    call(client, "priya", "ask", question=Q1)
    events, is_error = call(client, "jordan", "audit_query", filter={"user": "priya@companya.com"})
    assert not is_error and events["count"] >= 1
    for who in ("sam", "priya", "dana"):
        _, is_error = call(client, who, "audit_query", filter={})
        assert is_error, who
    blocked = rpc(client, "tools/call", {"name": "audit_query", "arguments": {}}, who="sam").json()["result"]
    assert "compliance" in blocked["content"][0]["text"]


# -- stateless, isolated, audited -------------------------------------------------------------------------------
def test_it_is_stateless_and_callers_never_see_each_other(rt_client):
    _, client = rt_client
    first = rpc(client, "tools/call", {"name": "search", "arguments": {"query": "database migration"}}, who="priya")
    assert not any("session" in h.lower() for h in first.headers)        # no session to hold or hijack
    order = ["priya", "sam", "priya", "sam"]
    results = [call(client, who, "search", query="database migration")[0]["results"] for who in order]
    assert results[0] and results[2] and results[0] == results[2]
    assert all("slack:C_DBMIGPRIV/thread-1" not in [h["doc_id"] for h in r] for r in results)
    assert results[1] == results[3] and [h["doc_id"] for h in results[1]] == []   # Sam has no access to DBMIG


def test_a_revocation_applies_to_the_next_mcp_call(rt_client):
    runtime, client = rt_client
    q = "auth service threat model concerns"
    assert "slack:C_AUTHPRIV/thread-1" in [h["doc_id"] for h in call(client, "priya", "search", query=q)[0]["results"]]
    runtime.advance("e2", True)
    assert "slack:C_AUTHPRIV/thread-1" not in [h["doc_id"] for h in call(client, "priya", "search", query=q)[0]["results"]]
    assert call(client, "priya", "get_source", doc_id="slack:C_AUTHPRIV/thread-1")[0] == {"found": False}


def test_every_call_is_logged_as_mcp_call_and_the_chain_still_verifies(rt_client):
    runtime, client = rt_client
    call(client, "priya", "search", query="database migration")
    call(client, "sam", "audit_query", filter={})                       # refused: still logged, with ok false
    calls = [e for e in runtime.audit_store.all() if e["event_type"] == "mcp_call"]
    seen = [(e["detail"]["tool"], e["detail"]["ok"], e["actor"]["client"]) for e in calls]
    assert seen == [("search", True, "mcp"), ("audit_query", False, "mcp")]
    assert calls[0]["actor"]["user_id"] == "priya@companya.com" and calls[1]["actor"]["roles"] == ["contractor"]
    assert call(client, "jordan", "audit_query", filter={})[0]["count"] >= 2
    assert client.get("/v1/audit/verify", headers={"Authorization": f"Bearer {token(client, 'jordan')}"}).json()["ok"] is True


def test_the_web_api_keeps_working_beside_mcp(rt_client):
    _, client = rt_client
    jwt = token(client, "priya")
    assert client.get("/v1/health").status_code == 200
    assert client.get("/v1/mywork", headers={"Authorization": f"Bearer {jwt}"}).status_code == 200
    assert client.post("/sim/reset").status_code == 404                  # dev login is off, so no /sim, MCP or not

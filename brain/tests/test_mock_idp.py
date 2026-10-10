"""Fix F2: the mock IdP signs fictional personas in with real tokens, so the whole API works with dev login off."""
import time

import jwt
import pytest
from fastapi.testclient import TestClient

from brain.api.app import create_app
from brain.auth import Authenticator, DevTokens, mint_token
from brain.config import Settings
from brain.runtime import fixture_runtime

KEY = "i" * 32
Q = "What's the status of the database migration, and were there blockers raised in Slack last week?"
BREACH = "Show me the security incident report from the Q3 breach"


def _client(**overrides) -> TestClient:
    settings = Settings(floor_latency_ms=0, jwt_signing_key=KEY, mock_idp=True, dev_auth=False, **overrides)
    return TestClient(create_app(fixture_runtime(settings)))


def _sign_in(client: TestClient, persona: str) -> dict:
    r = client.post("/idp/token", json={"persona": persona})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_the_route_does_not_exist_unless_enabled():
    off = TestClient(create_app(fixture_runtime(Settings(floor_latency_ms=0, jwt_signing_key=KEY, dev_auth=False))))
    assert off.post("/idp/token", json={"persona": "priya"}).status_code == 404


def test_enabling_it_without_a_key_is_refused_at_start():
    with pytest.raises(ValueError, match="JWT_SIGNING_KEY"):
        create_app(fixture_runtime(Settings(floor_latency_ms=0, mock_idp=True)))


def test_the_token_is_a_real_signed_jwt_with_the_personas_identity():
    r = _client().post("/idp/token", json={"persona": "jordan"}).json()
    claims = jwt.decode(r["access_token"], KEY, algorithms=["HS256"], audience="internal-brain")
    assert claims["email"] == "jordan@companya.com" and claims["roles"] == ["compliance"] and claims["iss"] == "mock-idp"
    assert r["token_type"] == "Bearer" and r["expires_in"] == 900 and 0 < claims["exp"] - claims["iat"] <= 900


def test_unknown_persona_gets_no_token():
    assert _client().post("/idp/token", json={"persona": "mallory"}).status_code == 404


def test_the_full_flow_works_with_dev_login_off():
    client = _client()
    priya, sam, jordan = (_sign_in(client, p) for p in ("priya", "sam", "jordan"))
    assert client.post("/v1/ask", json={"question": Q}, headers=priya).json()["citations"]
    refused = client.post("/v1/ask", json={"question": BREACH}, headers=sam).json()
    assert refused["refused"] and refused["citations"] == []
    assert client.post("/v1/audit/query", json={}, headers=sam).status_code == 403
    assert client.post("/v1/audit/query", json={}, headers=jordan).status_code == 200
    assert client.get("/v1/audit/verify", headers=jordan).json()["ok"] is True


def test_dev_tokens_are_refused_even_though_the_mock_idp_is_on():
    client = _client()
    assert client.post("/v1/ask", json={"question": Q}, headers={"Authorization": "Bearer dev:priya"}).status_code == 401
    assert client.post("/sim/reset").status_code == 404


def test_expired_wrong_key_and_wrong_audience_tokens_are_refused():
    client = _client()
    cases = {
        "expired": mint_token(KEY, "internal-brain", "priya@companya.com", ["engineer"], ttl_s=-5),
        "wrong key": mint_token("w" * 32, "internal-brain", "priya@companya.com", ["engineer"]),
        "wrong audience": mint_token(KEY, "someone-else", "priya@companya.com", ["engineer"]),
    }
    for name, token in cases.items():
        r = client.post("/v1/ask", json={"question": Q}, headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401, name


def test_a_token_without_an_expiry_is_refused():
    client = _client()
    token = jwt.encode({"sub": "priya@companya.com", "email": "priya@companya.com", "roles": ["engineer"],
                        "aud": "internal-brain"}, KEY, algorithm="HS256")
    assert client.post("/v1/ask", json={"question": Q}, headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_a_none_algorithm_token_is_refused():
    client = _client()
    forged = jwt.encode({"sub": "jordan@companya.com", "roles": ["compliance"], "aud": "internal-brain",
                         "exp": int(time.time()) + 300}, key="", algorithm="none")
    assert client.get("/v1/audit/verify", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_short_keys_are_rejected():
    with pytest.raises(ValueError, match="at least 32 bytes"):
        Authenticator(signing_key="short", audience="internal-brain", dev_auth=False)
    assert DevTokens(KEY).mint("priya@companya.com", ["engineer"])      # the test helper still mints with a good key


def test_token_lifetime_is_configurable():
    client = _client(mock_idp_ttl_s=60)
    r = client.post("/idp/token", json={"persona": "priya"}).json()
    assert r["expires_in"] == 60

"""`BRAIN_RUNTIME=fixture` honors the sign-in settings from the environment, so the mock IdP and the UI sign-in can be
tried with no database and no real account. Plain `fixture_runtime()` must keep ignoring the environment."""
import pytest
from fastapi.testclient import TestClient

from brain.api.app import create_app
from brain.runtime import fixture_runtime, fixture_settings_from_env

KEY = "f" * 32
Q = "What's the status of the database migration, and were there blockers raised in Slack last week?"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.setattr("brain.config.load_dotenv", lambda: None)          # never read a developer's real .env
    for name in ("BRAIN_MOCK_IDP", "BRAIN_MOCK_IDP_TTL_S", "BRAIN_DEV_AUTH", "JWT_SIGNING_KEY", "JWT_AUDIENCE",
                 "BRAIN_MCP", "BRAIN_PUBLIC_URL", "BRAIN_MCP_ALLOWED_HOSTS"):
        monkeypatch.delenv(name, raising=False)


def test_with_nothing_set_it_matches_plain_fixture_mode():
    s = fixture_settings_from_env()
    assert s.dev_auth is True and s.mock_idp is False and s.jwt_signing_key is None and s.floor_latency_ms == 0


def test_sign_in_settings_are_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("BRAIN_MOCK_IDP", "1")
    monkeypatch.setenv("BRAIN_MOCK_IDP_TTL_S", "120")
    monkeypatch.setenv("JWT_SIGNING_KEY", KEY)
    monkeypatch.setenv("JWT_AUDIENCE", "demo-aud")
    s = fixture_settings_from_env()
    assert s.mock_idp is True and s.mock_idp_ttl_s == 120 and s.jwt_signing_key == KEY and s.jwt_audience == "demo-aud"


@pytest.mark.parametrize("value,expected", [("0", False), (" 0 ", False), ("1", True), ("", True), ("yes", True)])
def test_dev_login_stays_on_unless_explicitly_set_to_zero(monkeypatch, value, expected):
    monkeypatch.setenv("BRAIN_DEV_AUTH", value)
    assert fixture_settings_from_env().dev_auth is expected


def test_the_whole_sign_in_flow_works_in_fixture_mode_with_dev_login_off(monkeypatch):
    monkeypatch.setenv("BRAIN_MOCK_IDP", "1")
    monkeypatch.setenv("JWT_SIGNING_KEY", KEY)
    monkeypatch.setenv("BRAIN_DEV_AUTH", "0")
    client = TestClient(create_app(fixture_runtime(fixture_settings_from_env())))
    token = client.post("/idp/token", json={"persona": "priya"}).json()["access_token"]
    assert client.post("/v1/ask", json={"question": Q}, headers={"Authorization": f"Bearer {token}"}).json()["citations"]
    assert client.post("/v1/ask", json={"question": Q}, headers={"Authorization": "Bearer dev:priya"}).status_code == 401
    assert client.post("/sim/reset").status_code == 404            # demo controls go away with dev login


def test_dev_login_and_the_mock_idp_can_run_side_by_side_for_the_demo(monkeypatch):
    monkeypatch.setenv("BRAIN_MOCK_IDP", "1")
    monkeypatch.setenv("JWT_SIGNING_KEY", KEY)
    client = TestClient(create_app(fixture_runtime(fixture_settings_from_env())))
    assert client.post("/idp/token", json={"persona": "sam"}).status_code == 200
    assert client.post("/v1/ask", json={"question": Q}, headers={"Authorization": "Bearer dev:sam"}).status_code == 200
    assert client.post("/sim/reset").status_code == 200


def test_plain_fixture_runtime_ignores_the_environment(monkeypatch):
    monkeypatch.setenv("BRAIN_MOCK_IDP", "1")
    monkeypatch.setenv("JWT_SIGNING_KEY", KEY)
    client = TestClient(create_app(fixture_runtime()))
    assert client.post("/idp/token", json={"persona": "priya"}).status_code == 404


def test_the_mock_idp_without_a_key_is_refused_at_start(monkeypatch):
    monkeypatch.setenv("BRAIN_MOCK_IDP", "1")
    with pytest.raises(ValueError, match="JWT_SIGNING_KEY"):
        create_app(fixture_runtime(fixture_settings_from_env()))


def test_mcp_settings_are_read_from_the_environment_too(monkeypatch):
    assert fixture_settings_from_env().mcp is False
    monkeypatch.setenv("BRAIN_MCP", "1")
    monkeypatch.setenv("BRAIN_PUBLIC_URL", "https://demo.example.sg")
    monkeypatch.setenv("BRAIN_MCP_ALLOWED_HOSTS", "demo.example.sg")
    s = fixture_settings_from_env()
    assert s.mcp is True and s.public_url == "https://demo.example.sg" and s.mcp_allowed_hosts == ("demo.example.sg",)

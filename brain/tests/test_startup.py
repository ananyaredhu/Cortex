"""Fix F1: development login is opt-in, and production refuses unsafe settings."""
import pytest
from fastapi.testclient import TestClient

from brain.api.app import create_app
from brain.config import Settings
from brain.runtime import fixture_runtime
from brain.startup import UnsafeConfiguration, check_startup

GOOD = dict(environment="production", jwt_signing_key="k" * 32, audit_signing_key="ab" * 32, denied_id_salt="a-real-secret",
            checker_model="cross-encoder/nli-deberta-v3-xsmall")


def test_dev_login_is_off_unless_explicitly_enabled(monkeypatch):
    monkeypatch.setattr("brain.config.load_dotenv", lambda: None)
    for value, expected in [(None, False), ("0", False), ("", False), ("true", False), ("1", True)]:
        monkeypatch.delenv("BRAIN_DEV_AUTH", raising=False)
        if value is not None:
            monkeypatch.setenv("BRAIN_DEV_AUTH", value)
        assert Settings.from_env().dev_auth is expected, value


def test_default_settings_do_not_serve_dev_tokens_or_sim_endpoints():
    app = create_app(fixture_runtime(Settings(floor_latency_ms=0)))
    client = TestClient(app)
    assert client.post("/v1/ask", json={"question": "hi"}, headers={"Authorization": "Bearer dev:jordan"}).status_code == 401
    assert client.post("/sim/reset").status_code == 404
    assert client.post("/sim/tamper", params={"seq": 1}).status_code == 404


def test_production_accepts_safe_settings():
    assert Settings(**GOOD).production_problems() == []
    assert check_startup(Settings(**GOOD)) == []


@pytest.mark.parametrize("change,needle", [
    ({"dev_auth": True}, "BRAIN_DEV_AUTH"),
    ({"jwt_signing_key": None}, "JWT_SIGNING_KEY is not set"),
    ({"jwt_signing_key": "short"}, "shorter than 32 bytes"),
    ({"audit_signing_key": None}, "AUDIT_SIGNING_KEY"),
    ({"denied_id_salt": "dev-salt"}, "AUDIT_DENIED_SALT"),
    ({"checker_model": "none"}, "CHECKER_MODEL"),
])
def test_production_refuses_each_unsafe_setting(change, needle):
    with pytest.raises(UnsafeConfiguration, match=needle):
        check_startup(Settings(**{**GOOD, **change}))


def test_production_refuses_the_fixture_runtime():
    with pytest.raises(UnsafeConfiguration, match="BRAIN_RUNTIME=fixture"):
        check_startup(Settings(**GOOD), fixture=True)


def test_production_lists_every_problem_at_once():
    with pytest.raises(UnsafeConfiguration) as exc:
        check_startup(Settings(environment="production", dev_auth=True))
    assert str(exc.value).count("\n- ") >= 5


def test_development_only_warns():
    warnings = check_startup(Settings(dev_auth=True, adp_app_key="k"))
    text = " ".join(warnings)
    assert "development login is ON" in text and "AUDIT_SIGNING_KEY" in text and "CHECKER_MODEL" in text
    assert "GENERATOR_MODEL is still the default label" in text      # fix F15: the audit log would name the wrong model


def test_fixture_mode_does_not_warn_about_missing_production_settings():
    assert check_startup(Settings(dev_auth=True), fixture=True) == []

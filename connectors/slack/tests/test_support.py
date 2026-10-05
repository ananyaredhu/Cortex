"""The pieces around the connector: the API client, the seed manifest and the .env loader."""
import os
from pathlib import Path

import pytest

from connectors.env import load_dotenv
from connectors.slack import SeedManifest, SlackClient, SlackError
from connectors.slack.fake import FakeSlack

EXAMPLE = Path(__file__).resolve().parents[1] / "seed-manifest.example.json"


def test_client_raises_slack_errors_and_needs_a_token(monkeypatch):
    client = FakeSlack().client()
    with pytest.raises(SlackError) as caught:
        client.call("conversations.info", channel="C0NOPE")
    assert caught.value.code == "channel_not_found"
    assert client.call("auth.test")["url"].startswith("https://")
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="SLACK_BOT_TOKEN is not set"):
        SlackClient.from_env()


def test_manifest_translates_both_ways(tmp_path):
    manifest = SeedManifest.load(EXAMPLE)
    real = manifest.real_doc("slack:C_DBMIG/thread-1")
    assert real == "slack:C0EXAMPLE01/1791619200.000100" and manifest.fixture_doc(real) == "slack:C_DBMIG/thread-1"
    assert manifest.real_token("channel:C_AUTH") == "channel:C0EXAMPLE03"
    assert manifest.fixture_token("channel:C0EXAMPLE03") == "channel:C_AUTH"
    assert manifest.real_token("public:org") == manifest.fixture_token("public:org") == "public:org"
    assert manifest.real_container("slack:C_PAYINC") == "slack:C0EXAMPLE05"
    assert manifest.fixture_container("slack:C0EXAMPLE05") == "slack:C_PAYINC"
    assert manifest.real_doc("slack:C_NOPE/thread-9") is None and manifest.fixture_doc("slack:C0NOPE/1.000000") is None
    manifest.save(tmp_path / "m.json")
    assert SeedManifest.load(tmp_path / "m.json") == manifest
    assert SeedManifest.load(tmp_path / "missing.json") == SeedManifest()


def test_dotenv_fills_only_what_is_unset_and_skips_empty_values(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# comment\nIB_TEST_A=from-file\nIB_TEST_B=\nIB_TEST_C='quoted value'   # trailing comment\n"
                   "IB_TEST_D=kept # note\nnot a line\n", encoding="utf-8")
    monkeypatch.setenv("IB_TEST_A", "from-shell")
    for name in ("IB_TEST_B", "IB_TEST_C", "IB_TEST_D"):
        monkeypatch.delenv(name, raising=False)
    load_dotenv(env)
    try:
        assert os.environ["IB_TEST_A"] == "from-shell"
        assert "IB_TEST_B" not in os.environ
        assert os.environ["IB_TEST_C"] == "quoted value" and os.environ["IB_TEST_D"] == "kept"
    finally:
        for name in ("IB_TEST_C", "IB_TEST_D"):
            os.environ.pop(name, None)
    load_dotenv(tmp_path / "missing.env")   # a missing file is fine

"""The deployment files (deploy/): what each container starts, and that no secret can ride into an image."""
import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"

_spec = importlib.util.spec_from_file_location("deploy_entry", DEPLOY / "entry.py")
entry = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(entry)


def test_ingestion_listens_for_events_only_for_the_sources_it_has(monkeypatch):
    monkeypatch.setenv("BRAIN_SOURCES", "confluence,jira")
    monkeypatch.setenv("SLACK_APP_TOKEN", "x")
    monkeypatch.setenv("GDRIVE_WEBHOOK_TOKEN", "x")
    cmd = entry.command("ingestion", [])
    assert cmd[1:] == ["-m", "connectors.ingestion", "--poll", "600", "--sources", "confluence,jira"]

    monkeypatch.setenv("BRAIN_SOURCES", "confluence, jira, slack, gdrive")
    monkeypatch.setenv("INGEST_POLL_SECONDS", "120")
    cmd = entry.command("ingestion", [])
    assert cmd[cmd.index("--poll") + 1] == "120" and cmd[cmd.index("--sources") + 1] == "confluence,jira,slack,gdrive"
    assert "--slack-events" in cmd and cmd[cmd.index("--drive-webhook") + 1] == "8110"

    monkeypatch.delenv("SLACK_APP_TOKEN")
    monkeypatch.delenv("GDRIVE_WEBHOOK_TOKEN")
    cmd = entry.command("ingestion", [])
    assert "--slack-events" not in cmd and "--drive-webhook" not in cmd      # the poll still covers both


def test_services_start_the_documented_apps_and_other_commands_pass_through():
    assert "brain.api.main:app" in entry.command("brain", []) and "8000" in entry.command("brain", [])
    assert "simulators.confluence.app:app" in entry.command("sim-confluence", [])
    assert "simulators.jira.app:app" in entry.command("sim-jira", [])
    assert entry.command("python", ["-m", "evals.leakci"]) == ["python", "-m", "evals.leakci"]


def test_account_files_are_linked_from_the_mounts_not_copied(tmp_path, monkeypatch):
    secrets, state, gdrive = tmp_path / "secrets", tmp_path / "state", tmp_path / "gdrive"
    for d in (secrets, state, gdrive):
        d.mkdir()
    (secrets / "token-gdrive-priya_at_companya.com.json").write_text("{}")
    (secrets / "identity-map.local.json").write_text("{}")
    monkeypatch.setenv("SECRETS_DIR", str(secrets))
    monkeypatch.setenv("STATE_DIR", str(state))
    monkeypatch.setattr(entry, "GDRIVE", gdrive)
    try:
        entry.link_account_files()
        entry.link_account_files()                                    # a restart links again without failing
    except OSError as exc:                                            # Windows without the symlink privilege
        import pytest
        pytest.skip(f"cannot create symlinks here: {type(exc).__name__}")
    assert sorted(p.name for p in gdrive.iterdir()) == ["token-gdrive-priya_at_companya.com.json", "watch-channels.local.json"]
    assert all(p.is_symlink() for p in gdrive.iterdir())


def test_every_setting_the_compose_file_asks_for_is_in_the_example():
    compose = (DEPLOY / "docker-compose.yml").read_text(encoding="utf8")
    example = {line.split("=", 1)[0] for line in (DEPLOY / ".env.example").read_text(encoding="utf8").splitlines()
               if line and not line.startswith("#") and "=" in line}
    asked = set(re.findall(r"\$\{([A-Z_]+)", compose)) - {"BRAIN_ENV"}     # BRAIN_ENV defaults to production on purpose
    assert asked <= example, sorted(asked - example)
    assert not [line for line in (DEPLOY / ".env.example").read_text(encoding="utf8").splitlines()
                if re.match(r"^[A-Z_]*(KEY|TOKEN|SECRET|PASSWORD|SALT)[A-Z_]*=\S", line)], "the example must hold names only"


def test_only_the_proxy_publishes_ports_and_it_never_forwards_the_demo_helpers():
    compose = (DEPLOY / "docker-compose.yml").read_text(encoding="utf8")
    assert compose.count("ports:") == 1 and compose.index("ports:") > compose.index("\n  web:")
    assert 'BRAIN_DEV_AUTH: "0"' in compose
    caddy = (DEPLOY / "Caddyfile").read_text(encoding="utf8")
    brain_paths = re.search(r"@brain path (.*)", caddy).group(1).split()
    assert not any(p.startswith("/sim") for p in brain_paths) and "handle /sim/*" in caddy


def test_secret_files_are_kept_out_of_the_build_context():
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf8").splitlines()
    for pattern in ("**/.env", "**/token*.json", "**/identity-map*.json", "**/seed-manifest*.json", "**/gdrive.local.json",
                    "**/credentials*.json", "**/*.local.json", "**/secrets/"):
        assert pattern in ignored, pattern

"""Container entry point (deploy/Dockerfile): `entry.py brain | ingestion | sim-confluence | sim-jira`, or any command.

Before starting, it links the account files that must not be in the image into the places the connectors read:
the Drive refresh tokens from the read-only secrets mount (SECRETS_DIR, default /run/brain-secrets), and Drive's
watch-channel list into the state volume (STATE_DIR, default /state) so it outlives the container. The other
account files have their own path settings (IDENTITY_MAP_PATH and friends), set in deploy/docker-compose.yml.
Prints file names only.
"""
import os
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
GDRIVE = APP / "connectors" / "gdrive"


def _link(target: Path, link: Path) -> None:
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(target)


def link_account_files() -> None:
    secrets = Path(os.environ.get("SECRETS_DIR") or "/run/brain-secrets")
    if secrets.is_dir():
        for token in sorted(secrets.glob("token-gdrive-*.json")):
            _link(token, GDRIVE / token.name)
            print(f"entry: linked {token.name}", flush=True)
    state = Path(os.environ.get("STATE_DIR") or "/state")
    if state.is_dir():
        _link(state / "watch-channels.local.json", GDRIVE / "watch-channels.local.json")


def sources() -> list[str]:
    return [s.strip() for s in (os.environ.get("BRAIN_SOURCES") or "confluence,jira").split(",") if s.strip()]


def command(role: str, rest: list[str]) -> list[str]:
    uvicorn = [sys.executable, "-m", "uvicorn", "--host", "0.0.0.0"]      # reachable on the private network only
    if role == "brain":
        return [*uvicorn, "brain.api.main:app", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*", *rest]
    if role == "sim-confluence":
        return [*uvicorn, "simulators.confluence.app:app", "--port", "8101", *rest]
    if role == "sim-jira":
        return [*uvicorn, "simulators.jira.app:app", "--port", "8102", *rest]
    if role == "ingestion":
        names = sources()
        cmd = [sys.executable, "-m", "connectors.ingestion", "--poll", os.environ.get("INGEST_POLL_SECONDS") or "600",
               "--sources", ",".join(names)]
        if "slack" in names and os.environ.get("SLACK_APP_TOKEN"):
            cmd.append("--slack-events")
        if "gdrive" in names and os.environ.get("GDRIVE_WEBHOOK_TOKEN"):
            cmd += ["--drive-webhook", "8110"]
        return [*cmd, *rest]
    return [role, *rest]


def main() -> None:
    args = sys.argv[1:] or ["brain"]
    link_account_files()
    cmd = command(args[0], args[1:])
    os.chdir(APP)
    os.execvp(cmd[0], cmd)


if __name__ == "__main__":
    main()

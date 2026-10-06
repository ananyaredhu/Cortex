"""Drive push notifications: open, list and stop one `changes.watch` channel per signed-in account.

    python -m connectors.gdrive.watch start https://<public host>/drive/notify
    python -m connectors.gdrive.watch status
    python -m connectors.gdrive.watch stop

Google then calls the address whenever anything changes in that account's Drive, not only under the root folders.
A notification names no file, so it only wakes ingestion (`python -m connectors.ingestion --poll ... --drive-webhook`),
which scans the root folders as it always does; nothing outside them is read. Every notification carries
GDRIVE_WEBHOOK_TOKEN (from .env), and the receiver drops anything without it.

Channels last at most a week. `start` stops the channels it opened before and opens new ones, so run it again to
renew. The open channels are kept in `watch-channels.local.json` (gitignored): IDs only, no secrets.
"""
import argparse
import json
import os
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from connectors.env import load_dotenv
from connectors.gdrive.client import DriveError, DriveSession, load_sessions

CHANNELS_PATH = Path(__file__).with_name("watch-channels.local.json")
MAX_SECONDS = 7 * 24 * 3600          # Google's limit for a changes channel
LIFETIME = MAX_SECONDS - 3600        # ask for a little less, so clocks that differ do not get the request refused
MIN_TOKEN_CHARS = 24


@dataclass
class Channel:
    account: str          # the canonical email of the signed-in account it watches
    id: str
    resource_id: str
    expiration_ms: int
    address: str


def open_channels(sessions: dict[str, DriveSession], address: str, token: str, *, lifetime: int = LIFETIME,
                  now=time.time) -> list[Channel]:
    """One channel per account, starting from that account's current change position."""
    if not address.startswith("https://"):
        raise ValueError("Google only calls HTTPS addresses with a valid certificate")
    channels = []
    for account, session in sorted(sessions.items()):
        start = session.json("changes/startPageToken")["startPageToken"]
        body = {"id": str(uuid.uuid4()), "type": "web_hook", "address": address, "token": token,
                "expiration": int((now() + lifetime) * 1000)}
        opened = session.request("changes/watch", method="POST", body=body, pageToken=start).json()
        channels.append(Channel(account, opened["id"], opened["resourceId"], int(opened["expiration"]), address))
    return channels


def stop_channels(sessions: dict[str, DriveSession], channels: list[Channel]) -> list[str]:
    """Stop each channel through the account that opened it. Returns problems; one already gone is not a problem."""
    problems = []
    for channel in channels:
        session = sessions.get(channel.account)
        if session is None:
            problems.append(f"{channel.account}: not signed in any more, its channel runs until it expires")
            continue
        try:
            session.request("channels/stop", method="POST", body={"id": channel.id, "resourceId": channel.resource_id})
        except DriveError as exc:
            if exc.status != 404:
                problems.append(f"{channel.account}: could not stop its channel ({exc.status})")
    return problems


def load_channels(path: Path = CHANNELS_PATH) -> list[Channel]:
    if not path.exists():
        return []
    return [Channel(**c) for c in json.loads(path.read_text(encoding="utf-8"))]


def save_channels(channels: list[Channel], path: Path = CHANNELS_PATH) -> None:
    path.write_text(json.dumps([asdict(c) for c in channels], indent=2) + "\n", encoding="utf-8")


def webhook_token() -> str:
    token = (os.environ.get("GDRIVE_WEBHOOK_TOKEN") or "").strip()
    if len(token) < MIN_TOKEN_CHARS:
        raise RuntimeError(f"GDRIVE_WEBHOOK_TOKEN is not set or shorter than {MIN_TOKEN_CHARS} characters: put a random "
                           "value in .env, e.g. from: python -c \"import secrets; print(secrets.token_urlsafe(32))\"")
    return token


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m connectors.gdrive.watch", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start", help="(re)open a channel per signed-in account")
    start.add_argument("address", help="the public HTTPS address of the receiver, ending in /drive/notify")
    sub.add_parser("status", help="list the open channels")
    sub.add_parser("stop", help="stop every open channel")
    args = parser.parse_args()
    load_dotenv()
    channels = load_channels()
    if args.command == "status":
        for c in channels or []:
            hours = (c.expiration_ms / 1000 - time.time()) / 3600
            print(f"{c.account}: {'expires in %.1f h' % hours if hours > 0 else 'expired'}  ->  {c.address}")
        print(f"{len(channels)} channel(s)")
        return
    sessions = load_sessions()
    problems = stop_channels(sessions, channels)
    save_channels([])
    if args.command == "start":
        opened = open_channels(sessions, args.address, webhook_token())
        save_channels(opened)
        print(f"opened {len(opened)} channel(s), for {', '.join(c.account for c in opened)}; they expire in about 7 days")
    else:
        print(f"stopped {len(channels) - len(problems)} channel(s)")
    for problem in problems:
        print("PROBLEM:", problem, file=sys.stderr)


if __name__ == "__main__":
    main()

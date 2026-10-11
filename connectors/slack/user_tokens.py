"""Per-person Slack user tokens, for DMs (docs/02-contracts/acl-model.md, "Slack DMs").

A bot cannot read a DM between two people. Each person who agrees authorizes the app once
(`python -m connectors.slack.authorize <canonical email>`), which stores a read-only user token (`xoxp-`, scopes
`im:read`, `im:history`, `mpim:read`, `mpim:history`) in `token-slack-<persona>.json`: gitignored, never logged,
only ever sent in the Authorization header.

The files live next to this module, or in `SLACK_USER_TOKENS_DIR` (deploy/ points it at the secrets mount).
No files means nobody has authorized, and the connector reads no DMs.
"""
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from connectors.slack.client import SlackClient

TOKEN_DIR = Path(__file__).parent
TOKEN_GLOB = "token-slack-*.json"      # matched by `token*.json` in .gitignore
USER_SCOPES = ("im:history", "im:read", "mpim:history", "mpim:read")


def token_dir() -> Path:
    return Path(os.environ.get("SLACK_USER_TOKENS_DIR") or TOKEN_DIR)


def token_path(canonical_email: str, directory: Path | None = None) -> Path:
    return (directory or token_dir()) / f"token-slack-{canonical_email.strip().lower().replace('@', '_at_')}.json"


@dataclass(frozen=True)
class UserGrant:
    """One person's authorization: who they are in Slack, and a client that reads as them."""
    email: str            # canonical
    user_id: str
    client: SlackClient


class UserTokens:
    """Who has authorized, by canonical email. `refresh` re-reads the files, so a new grant needs no restart."""

    def __init__(self, grants: dict[str, UserGrant] | None = None, *, directory: Path | None = None,
                 client_for: Callable[[str], SlackClient] = SlackClient.from_token) -> None:
        self._fixed = grants is not None      # tests hand the grants over; there is nothing to re-read
        self._grants = dict(grants or {})
        self._directory = directory
        self._client_for = client_for
        self._seen: dict[str, tuple[int, int]] = {}   # file name -> (mtime, size) it was last loaded at
        if not self._fixed:
            self.refresh()

    @classmethod
    def none(cls) -> "UserTokens":
        return cls({})

    def refresh(self) -> None:
        """Load new or changed token files and forget the ones that are gone. A file that cannot be read, or
        that is not a user token for the person it names, is skipped: that person has not authorized."""
        if self._fixed:
            return
        seen: dict[str, tuple[int, int]] = {}
        grants: dict[str, UserGrant] = {}
        for file in sorted((self._directory or token_dir()).glob(TOKEN_GLOB)):
            try:
                stat = file.stat()
                stamp = (stat.st_mtime_ns, stat.st_size)
                old = next((g for g in self._grants.values() if token_path(g.email, file.parent) == file), None)
                if self._seen.get(file.name) == stamp:      # unchanged: keep the grant, or keep it dropped
                    seen[file.name] = stamp
                    if old is not None:
                        grants[old.email] = old
                    continue
                data = json.loads(file.read_text(encoding="utf-8"))
                email, user_id, token = (str(data[k]).strip() for k in ("canonical_email", "user_id", "access_token"))
            except (OSError, ValueError, KeyError, TypeError):
                continue
            email = email.lower()
            if not email or not user_id or not token.startswith("xoxp-") or token_path(email, file.parent) != file:
                continue
            grants[email], seen[file.name] = UserGrant(email, user_id, self._client_for(token)), stamp
        self._grants, self._seen = grants, seen

    def get(self, canonical_email: str) -> UserGrant | None:
        return self._grants.get(canonical_email.strip().lower())

    def drop(self, canonical_email: str) -> None:
        """Slack said this token no longer works (revoked): stop using it until the file changes."""
        self._grants.pop(canonical_email.strip().lower(), None)

    def all(self) -> list[UserGrant]:
        return [self._grants[email] for email in sorted(self._grants)]

    def __len__(self) -> int:
        return len(self._grants)

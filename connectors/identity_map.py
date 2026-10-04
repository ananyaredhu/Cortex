"""Canonical email <-> platform account, for connectors over real platforms (acl-model.md, "Identity mapping").

Our real Slack and Drive personas are personal accounts whose addresses differ from the canonical ones
(`priya@companya.com`). Tokens, `Document.author` and `PlatformIdentity.email` always use the canonical
email, so each real connector translates through this map at its edge.

The map is local configuration and is never committed: copy `identity-map.example.json` to
`identity-map.local.json` (gitignored) or point `IDENTITY_MAP_PATH` at a file. Fail closed in both
directions: an account that is not in the map resolves to nothing and contributes no token.
"""
import json
import os
from pathlib import Path

from connectors.base import Source

DEFAULT_PATH = Path(__file__).with_name("identity-map.local.json")
_SOURCES = ("slack", "gdrive", "confluence", "jira")


def _norm(value: str) -> str:
    return value.strip().lower()


class IdentityMap:
    def __init__(self, accounts: dict[str, dict[str, str]] | None = None) -> None:
        """`accounts` is {canonical email: {source: platform account}}. Raises ValueError if it is ambiguous."""
        self._to_platform: dict[tuple[str, str], str] = {}
        self._to_canonical: dict[tuple[str, str], str] = {}
        for canonical, per_source in (accounts or {}).items():
            if not isinstance(canonical, str) or not isinstance(per_source, dict):
                raise ValueError("identity map: `accounts` must map a canonical email to {source: account}")
            for source, account in per_source.items():
                if source not in _SOURCES:
                    raise ValueError(f"identity map: unknown source {source!r} (expected one of {', '.join(_SOURCES)})")
                if not isinstance(account, str) or not account.strip() or not canonical.strip():
                    raise ValueError(f"identity map: empty or non-text entry under source {source!r}")
                forward, backward = (source, _norm(canonical)), (source, _norm(account))
                if forward in self._to_platform:
                    raise ValueError(f"identity map: a canonical email is listed twice for {source}")
                if backward in self._to_canonical:
                    # Two people on one account would let either read as the other.
                    raise ValueError(f"identity map: one {source} account is mapped to two canonical emails")
                self._to_platform[forward] = _norm(account)
                self._to_canonical[backward] = _norm(canonical)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "IdentityMap":
        """The map from `path`, else `IDENTITY_MAP_PATH`, else `identity-map.local.json` next to this module.

        A missing file is an empty map (nobody resolves). A file that exists but is malformed raises
        ValueError: a broken config should be noticed, not silently deny everyone.
        """
        path = Path(path or os.environ.get("IDENTITY_MAP_PATH") or DEFAULT_PATH)
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"identity map: cannot read {path.name}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("accounts"), dict):
            raise ValueError(f"identity map: {path.name} needs a top-level `accounts` object")
        return cls(data["accounts"])

    def platform_account(self, source: Source, canonical_email: str) -> str | None:
        """The account this person uses on `source`, or None when unmapped."""
        return self._to_platform.get((source, _norm(canonical_email)))

    def canonical_email(self, source: Source, platform_account: str) -> str | None:
        """Whose account this is, or None when unmapped (then it contributes no token and is nobody's author)."""
        return self._to_canonical.get((source, _norm(platform_account)))

    def canonical_emails(self, source: Source) -> list[str]:
        """Everyone mapped on `source`."""
        return sorted(email for (src, email) in self._to_platform if src == source)

    def __len__(self) -> int:
        return len(self._to_platform)

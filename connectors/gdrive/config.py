"""Local configuration for the Drive connector. Never committed: `gdrive.local.json` is gitignored.

Personal Google accounts have no organisation, no domain-wide delegation and no readable group membership, so
three things Drive cannot tell us are declared here instead:

- `root_folders`: the only folders the connector may read. The persona accounts are people's own Google accounts,
  full of files that have nothing to do with Company A. Anything outside these folders does not exist for the
  connector. No roots configured means nothing is read.
- `groups`: who is in each group, by canonical email, and optionally the Google Group address files are shared with.
- `org_domains`: canonical-email domains that count as inside the organisation. Everyone else is `external:`.

No Drive file ever carries `public:org`: there is nothing in a consumer Drive that means "the whole organisation".
"""
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PATH = Path(__file__).with_name("gdrive.local.json")


def _norm(value: str) -> str:
    return value.strip().lower()


@dataclass(frozen=True)
class Group:
    members: frozenset[str]            # canonical emails
    address: str | None = None         # Google Group address files are shared with, when there is a real group


@dataclass
class DriveConfig:
    root_folders: list[str] = field(default_factory=list)
    groups: dict[str, Group] = field(default_factory=dict)
    org_domains: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "DriveConfig":
        groups: dict[str, Group] = {}
        for name, spec in (data.get("groups") or {}).items():
            if isinstance(spec, list):
                spec = {"members": spec}
            if not isinstance(spec, dict) or not isinstance(spec.get("members", []), list):
                raise ValueError(f"gdrive config: group {name!r} needs a `members` list")
            address = spec.get("address")
            groups[str(name)] = Group(frozenset(_norm(m) for m in spec.get("members", [])), _norm(address) if address else None)
        roots = data.get("root_folders") or []
        domains = data.get("org_domains") or []
        if not isinstance(roots, list) or not isinstance(domains, list):
            raise ValueError("gdrive config: `root_folders` and `org_domains` must be lists")
        return cls([str(r).strip() for r in roots if str(r).strip()], groups, [_norm(d).lstrip("@") for d in domains])

    @classmethod
    def load(cls, path: str | Path | None = None) -> "DriveConfig":
        """From `path`, else `GDRIVE_CONFIG_PATH`, else `gdrive.local.json` here. Missing file: empty, so nothing is in scope."""
        path = Path(path or os.environ.get("GDRIVE_CONFIG_PATH") or DEFAULT_PATH)
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"gdrive config: cannot read {path.name}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"gdrive config: {path.name} must hold an object")
        return cls.from_dict(data)

    def is_internal(self, canonical_email: str) -> bool:
        return _norm(canonical_email).rpartition("@")[2] in self.org_domains

    def groups_of(self, canonical_email: str) -> list[str]:
        return sorted(name for name, group in self.groups.items() if _norm(canonical_email) in group.members)

    def group_by_address(self, address: str) -> str | None:
        return next((name for name, group in self.groups.items() if group.address and group.address == _norm(address)), None)

"""Seed manifest: fixture IDs <-> the IDs a real workspace assigned (connector-interface.md, "Document granularity and IDs").

The fixtures call a channel `C_DBMIG` and a thread `slack:C_DBMIG/thread-1`. A real workspace calls them
something like `C07ABC123` and `slack:C07ABC123/1791619200.000100`. The connector always speaks real IDs. The
manifest lets the contract tests, the golden cases and the demo scripts find the seeded documents.

It holds no secrets, but it describes one particular workspace, so the real one lives in
`seed-manifest.local.json` (gitignored). `seed-manifest.example.json` shows the shape.
"""
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PATH = Path(__file__).with_name("seed-manifest.local.json")


@dataclass
class SeedManifest:
    channels: dict[str, str] = field(default_factory=dict)   # fixture channel id -> real channel id
    threads: dict[str, str] = field(default_factory=dict)    # fixture doc id -> real thread ts

    @classmethod
    def load(cls, path: str | Path | None = None) -> "SeedManifest":
        """From `path`, else `SLACK_SEED_MANIFEST_PATH`, else `seed-manifest.local.json` here. Missing file: empty."""
        path = Path(path or os.environ.get("SLACK_SEED_MANIFEST_PATH") or DEFAULT_PATH)
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(dict(data.get("channels") or {}), dict(data.get("threads") or {}))

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps({"channels": self.channels, "threads": self.threads}, indent=2) + "\n", encoding="utf-8")

    # -- fixture -> real --------------------------------------------------------------------------
    def real_doc(self, fixture_doc_id: str) -> str | None:
        ts = self.threads.get(fixture_doc_id)
        channel = self.channels.get(fixture_doc_id.split(":", 1)[-1].split("/", 1)[0])
        return f"slack:{channel}/{ts}" if ts and channel else None

    def real_container(self, fixture_container_id: str) -> str | None:
        channel = self.channels.get(fixture_container_id.split(":", 1)[-1])
        return f"slack:{channel}" if channel else None

    def real_token(self, token: str) -> str:
        kind, _, name = token.partition(":")
        return f"channel:{self.channels[name]}" if kind == "channel" and name in self.channels else token

    # -- real -> fixture --------------------------------------------------------------------------
    def fixture_doc(self, real_doc_id: str) -> str | None:
        for fixture_id in self.threads:
            if self.real_doc(fixture_id) == real_doc_id:
                return fixture_id
        return None

    def fixture_container(self, real_container_id: str) -> str | None:
        real = real_container_id.split(":", 1)[-1]
        return next((f"slack:{fixture}" for fixture, r in self.channels.items() if r == real), None)

    def fixture_token(self, token: str) -> str:
        kind, _, name = token.partition(":")
        if kind != "channel":
            return token
        return next((f"channel:{fixture}" for fixture, real in self.channels.items() if real == name), token)

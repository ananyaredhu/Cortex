"""Seed manifest: fixture IDs <-> the IDs a real Drive assigned (connector-interface.md, "Document granularity and IDs").

The fixtures call a file `gdrive:postmortem-pay-outage` in folder `gdrive:folder-incidents`. A real Drive calls them
something like `gdrive:1AbC...xyz` and `gdrive:1DeF...uvw`. The connector always speaks real IDs. The manifest lets
the contract tests, the golden cases and the demo scripts find the seeded files.

Drive's ACL tokens name people and groups, never files or folders, so only document and container IDs need
translating. It holds no secrets, but it describes one particular Drive, so the real one lives in
`seed-manifest.local.json` (gitignored). `seed-manifest.example.json` shows the shape.
"""
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PATH = Path(__file__).with_name("seed-manifest.local.json")


@dataclass
class SeedManifest:
    folders: dict[str, str] = field(default_factory=dict)    # fixture folder id -> real folder id
    files: dict[str, str] = field(default_factory=dict)      # fixture doc id -> real file id
    versions: dict[str, str] = field(default_factory=dict)   # fixture doc id -> the file's version when the manifest was built

    @classmethod
    def load(cls, path: str | Path | None = None) -> "SeedManifest":
        """From `path`, else `GDRIVE_SEED_MANIFEST_PATH`, else `seed-manifest.local.json` here. Missing file: empty."""
        path = Path(path or os.environ.get("GDRIVE_SEED_MANIFEST_PATH") or DEFAULT_PATH)
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(dict(data.get("folders") or {}), dict(data.get("files") or {}), dict(data.get("versions") or {}))

    def save(self, path: str | Path) -> None:
        data = {"folders": self.folders, "files": self.files, "versions": self.versions}
        Path(path).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    # -- fixture -> real --------------------------------------------------------------------------
    def real_doc(self, fixture_doc_id: str) -> str | None:
        file_id = self.files.get(fixture_doc_id)
        return f"gdrive:{file_id}" if file_id else None

    def real_container(self, fixture_container_id: str) -> str | None:
        folder_id = self.folders.get(fixture_container_id.split(":", 1)[-1])
        return f"gdrive:{folder_id}" if folder_id else None

    # -- real -> fixture --------------------------------------------------------------------------
    def fixture_doc(self, real_doc_id: str) -> str | None:
        real = real_doc_id.split(":", 1)[-1]
        return next((fixture for fixture, r in self.files.items() if r == real), None)

    def fixture_container(self, real_container_id: str) -> str | None:
        real = real_container_id.split(":", 1)[-1]
        return next((f"gdrive:{fixture}" for fixture, r in self.folders.items() if r == real), None)

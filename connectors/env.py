"""Load `.env` for command-line entry points, without printing or logging any value."""
import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def load_dotenv(path: str | Path | None = None) -> None:
    """Set variables from `.env` (repo root by default) that are not already set. A missing file is fine.

    Lines are `NAME=value`, optionally quoted; `#` starts a comment after whitespace. Empty values are skipped,
    so an unfilled name in `.env` does not hide a default.
    """
    file = Path(path) if path else _ROOT / ".env"
    if not file.exists():
        return
    for raw in file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        value = value.split(" #", 1)[0].split("\t#", 1)[0].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        name = name.strip()
        if name and value and name not in os.environ:
            os.environ[name] = value

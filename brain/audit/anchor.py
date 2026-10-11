"""Where the newest signed head of the audit log is kept, outside the database (fix F4).

Every `checkpoint_every` events a signed checkpoint goes into the log itself, but anyone who can write to the database can
delete the newest events and the rest of the chain still verifies. So on *every* append the store signs the new head
(`seq`, `hash`) and hands it to an anchor that lives somewhere the database's writers cannot reach. `/verify` compares the log
with the last head recorded there: a log that ends earlier, or an event at that position that changed, is tampering.

`FileAnchor` is an append-only file of JSON lines. Put it on a different volume or host than the database, or ship it on to
somewhere write-once; an anchor in the same place as the log protects against nothing. `MemoryAnchor` is for tests and the
fixture demo.
"""
import json
import os
import threading
from pathlib import Path
from typing import Protocol

from . import chain


class HeadAnchor(Protocol):
    def publish(self, head: dict) -> None: ...
    def latest(self) -> dict | None: ...


def sign_head(seq: int, head_hash: str, signer: chain.Signer, ts: str) -> dict:
    """The record an anchor keeps: the head and an Ed25519 signature over `seq:hash` (the checkpoint's message)."""
    return {"seq": seq, "hash": head_hash, "ts": ts, "public_key": signer.public_key_hex,
            "signature": signer.sign(f"{seq}:{head_hash}")}


class MemoryAnchor:
    def __init__(self) -> None:
        self._heads: list[dict] = []
        self._lock = threading.Lock()

    def publish(self, head: dict) -> None:
        with self._lock:
            self._heads.append(dict(head))

    def latest(self) -> dict | None:
        with self._lock:
            return dict(self._heads[-1]) if self._heads else None


class FileAnchor:
    """One JSON line per head, appended and flushed to disk. `latest` is the last line that parses (a torn final line from a
    crash is skipped; earlier lines stay as the history)."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def publish(self, head: dict) -> None:
        line = json.dumps(head, sort_keys=True, separators=(",", ":")) + "\n"
        with self._lock, open(self._path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

    def latest(self) -> dict | None:
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return None
        for line in reversed(lines):
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict) and {"seq", "hash", "signature", "public_key"} <= set(value):
                return value
        return None

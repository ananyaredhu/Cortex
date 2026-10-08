"""Hash chain and signed checkpoints (audit-event-schema.md, "Hash chain").

`hash = SHA256(canonical_json(event without "hash"))`, where the canonical form includes `prev_hash`; the first
event's `prev_hash` is `GENESIS`. Every `checkpoint_every` events a `checkpoint` event signs the head hash with an
Ed25519 key. Any edit, deletion, insertion or reordering breaks the chain at that point.

Pure functions over dictionaries, so the same code verifies rows read back from Postgres. Keep floats out of
events: jsonb would re-render them and the canonical form must survive the round trip.
"""
import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

GENESIS = "sha256:" + "0" * 64


def canonical(event: dict) -> bytes:
    body = {k: v for k, v in event.items() if k != "hash"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def event_hash(event: dict) -> str:
    return "sha256:" + hashlib.sha256(canonical(event)).hexdigest()


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def link(event: dict, seq: int, prev_hash: str) -> dict:
    """The event with `seq`, `prev_hash` and `hash` set."""
    out = {k: v for k, v in event.items() if k != "hash"}
    out["seq"] = seq
    out["prev_hash"] = prev_hash
    out["hash"] = event_hash(out)
    return out


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    checked: int
    checkpoints: int
    first_broken_seq: int | None = None
    reason: str | None = None

    def as_json(self) -> dict:
        if self.ok:
            return {"ok": True, "checked": self.checked, "checkpoints": self.checkpoints}
        return {"ok": False, "first_broken_seq": self.first_broken_seq, "reason": self.reason,
                "checked": self.checked, "checkpoints": self.checkpoints}


class Signer:
    """Ed25519 for checkpoints. `seed` is 32 bytes as hex (AUDIT_SIGNING_KEY); None makes a key for this process."""

    def __init__(self, seed_hex: str | None = None) -> None:
        if seed_hex:
            self._private = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(seed_hex))
        else:
            self._private = Ed25519PrivateKey.generate()
        self.public_key_hex = self._private.public_key().public_bytes_raw().hex()

    def sign(self, message: str) -> str:
        return self._private.sign(message.encode()).hex()

    @staticmethod
    def verify(public_key_hex: str, message: str, signature_hex: str) -> bool:
        try:
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex)).verify(bytes.fromhex(signature_hex), message.encode())
            return True
        except (InvalidSignature, ValueError):
            return False


def checkpoint_event(head_seq: int, head_hash: str, signer: Signer, ts: str) -> dict:
    message = f"{head_seq}:{head_hash}"
    return {"ts": ts, "request_id": f"ckpt_{head_seq}", "event_type": "checkpoint",
            "actor": {"user_id": "system:audit", "roles": [], "client": "audit"},
            "checkpoint": {"upto_seq": head_seq, "head_hash": head_hash,
                           "public_key": signer.public_key_hex, "signature": signer.sign(message)}}


def verify(events: Iterable[dict], *, public_key_hex: str | None = None) -> VerifyResult:
    """Walk the chain in order. Checkpoints are verified against `public_key_hex` when given, otherwise against the
    key they name (which proves integrity of the chain, not who signed it)."""
    prev = GENESIS
    expected_seq = 1
    checked = checkpoints = 0
    for event in events:
        seq = event.get("seq")
        if seq != expected_seq:
            return VerifyResult(False, checked, checkpoints, seq, f"sequence gap: expected {expected_seq}")
        if event.get("prev_hash") != prev:
            return VerifyResult(False, checked, checkpoints, seq, "prev_hash does not match the previous event")
        if event.get("hash") != event_hash(event):
            return VerifyResult(False, checked, checkpoints, seq, "hash mismatch: the event was modified")
        if event.get("event_type") == "checkpoint":
            cp = event.get("checkpoint") or {}
            key = public_key_hex or cp.get("public_key", "")
            message = f"{cp.get('upto_seq')}:{cp.get('head_hash')}"
            if cp.get("head_hash") != prev or not Signer.verify(key, message, cp.get("signature", "")):
                return VerifyResult(False, checked, checkpoints, seq, "checkpoint signature does not verify")
            checkpoints += 1
        prev = event["hash"]
        expected_seq += 1
        checked += 1
    return VerifyResult(True, checked, checkpoints)

"""Fix F3: a restart must not make `/verify` report tampering, and a configured key must still be pinned."""
from brain.audit import chain
from brain.audit.service import AuditService
from brain.audit.store import MemoryAuditStore
from brain.config import Settings
from brain.runtime import fixture_runtime

SEED_A = "11" * 32
SEED_B = "22" * 32


def _event(i: int) -> dict:
    return {"ts": f"2026-10-10T10:00:{i:02d}Z", "request_id": f"req_{i}", "event_type": "ask",
            "actor": {"user_id": "priya@companya.com", "roles": ["engineer"], "client": "ui"},
            "query": {"text": f"question {i}", "skill": None}, "decisions": [], "flags": []}


def _filled(signer: chain.Signer) -> MemoryAuditStore:
    store = MemoryAuditStore(signer=signer, checkpoint_every=2)
    for i in range(1, 6):
        store.append(_event(i))
    return store


def _service_over(events: list[dict], signer: chain.Signer):
    """An AuditService whose store holds `events` from an earlier run, as after a restart."""
    store = MemoryAuditStore(signer=signer, checkpoint_every=2)
    store._events = list(events)
    return AuditService(store, fixture_runtime(Settings(floor_latency_ms=0)).brain)


def test_only_a_configured_key_can_be_pinned():
    assert MemoryAuditStore(signer=chain.Signer()).pinned_public_key_hex is None
    configured = MemoryAuditStore(signer=chain.Signer(SEED_A))
    assert configured.pinned_public_key_hex == configured.public_key_hex


def test_restart_with_a_made_up_key_is_not_reported_as_tampering():
    earlier_run = _filled(chain.Signer())                                   # no AUDIT_SIGNING_KEY: a key for that run only
    after_restart = _service_over(earlier_run.all(), chain.Signer())        # new run, new made-up key
    result = after_restart.verify()
    assert result["ok"] is True and result["checkpoints"] >= 2
    assert result["signer_pinned"] is False                                  # and the response says the guarantee is weaker


def test_the_old_behavior_would_have_failed():
    """The bug: checking an earlier run's checkpoints against this run's key."""
    earlier_run = _filled(chain.Signer())
    new_key = chain.Signer().public_key_hex
    assert chain.verify(earlier_run.all(), public_key_hex=new_key).ok is False


def test_a_configured_key_survives_a_restart_and_is_pinned():
    earlier_run = _filled(chain.Signer(SEED_A))
    result = _service_over(earlier_run.all(), chain.Signer(SEED_A)).verify()
    assert result["ok"] is True and result["signer_pinned"] is True


def test_a_pinned_key_rejects_checkpoints_signed_by_another_key():
    forged_run = _filled(chain.Signer(SEED_B))                               # someone re-signed with their own key
    result = _service_over(forged_run.all(), chain.Signer(SEED_A)).verify()
    assert result["ok"] is False and "checkpoint signature" in result["reason"]


def test_editing_an_entry_is_still_caught_without_a_pinned_key():
    events = _filled(chain.Signer()).all()
    events[1]["query"]["text"] = "TAMPERED"
    result = _service_over(events, chain.Signer()).verify()
    assert result["ok"] is False and result["first_broken_seq"] == 2

"""Fix F4: deleting the newest audit events is noticed, because every append records a signed head outside the log."""
import json
from types import SimpleNamespace

import pytest

from brain.audit import chain
from brain.audit.anchor import FileAnchor, MemoryAnchor, sign_head
from brain.audit.service import AuditService
from brain.audit.store import MemoryAuditStore, PostgresAuditStore
from brain.config import Settings
from brain.runtime import fixture_runtime

SEED = "11" * 32


def event(i: int) -> dict:
    return {"request_id": f"r{i}", "event_type": "ask", "actor": {"user_id": "priya@companya.com", "roles": [], "client": "ui"},
            "query": {"text": f"question {i}", "skill": None}, "decisions": [], "flags": []}


def service(store) -> AuditService:
    return AuditService(store, brain=SimpleNamespace(settings=Settings()))     # verify() reads only the store


def filled(anchor, n=10, **kwargs) -> MemoryAuditStore:
    store = MemoryAuditStore(signer=chain.Signer(SEED), checkpoint_every=100, anchor=anchor, **kwargs)
    for i in range(n):
        store.append(event(i))
    return store


# -- detection ---------------------------------------------------------------------------------------------------------------
def test_an_untouched_log_verifies_and_says_it_is_anchored():
    out = service(filled(MemoryAnchor())).verify()
    assert out["ok"] and out["head_anchored"] and out["signer_pinned"]


def test_deleting_the_newest_events_is_detected_even_though_the_rest_of_the_chain_is_fine():
    store = filled(MemoryAnchor())
    store.truncate(7)
    assert chain.verify(store.all(), public_key_hex=store.pinned_public_key_hex).ok             # the old check alone sees nothing
    out = service(store).verify()
    assert out["ok"] is False and out["first_broken_seq"] == 8
    assert "ends at event 7" in out["reason"] and "newest events were deleted" in out["reason"]


def test_deleting_a_single_newest_event_is_detected():
    store = filled(MemoryAnchor())
    store.truncate(9)
    assert service(store).verify()["ok"] is False


def test_replacing_history_and_rebuilding_the_chain_is_detected():
    anchor = MemoryAnchor()
    store = filled(anchor)
    forged = MemoryAuditStore(signer=chain.Signer(SEED), checkpoint_every=100)
    for i in range(10):
        forged.append({**event(i), "query": {"text": "something else", "skill": None}} if i == 4 else event(i))
    store._events = forged.all()                                                  # a consistent chain with one event rewritten
    assert chain.verify(store.all(), public_key_hex=store.pinned_public_key_hex).ok
    out = service(store).verify()
    assert out["ok"] is False and "replaced" in out["reason"]


def test_a_head_with_a_bad_signature_is_refused():
    anchor = MemoryAnchor()
    store = filled(anchor)
    head = anchor.latest()
    anchor.publish({**head, "signature": "00" * 64})
    assert "does not verify" in service(store).verify()["reason"]


def test_events_with_no_head_on_record_are_refused_but_an_empty_log_is_fine():
    assert service(MemoryAuditStore(signer=chain.Signer(SEED), anchor=MemoryAnchor())).verify()["ok"]
    store = filled(None)
    store.anchor = MemoryAnchor()                                                # an anchor added later, with nothing recorded
    assert "no signed head" in service(store).verify()["reason"]


def test_adopting_the_head_once_lets_an_existing_log_start_being_anchored():
    store = filled(None)
    store.anchor = MemoryAnchor()
    store.adopt_head()
    assert service(store).verify()["ok"]
    store.append(event(99))
    assert service(store).verify()["ok"]
    store.truncate(10)
    assert service(store).verify()["ok"] is False


def test_an_anchor_behind_the_log_is_fine_because_a_crash_can_leave_it_so():
    anchor = MemoryAnchor()
    store = filled(anchor, n=5)
    store._events.append(chain.link(event(5), 6, store._events[-1]["hash"]))     # stored, but its head was never published
    assert service(store).verify()["ok"]


def test_a_store_without_an_anchor_behaves_as_before():
    store = filled(None)
    store.truncate(5)
    out = service(store).verify()
    assert out["ok"] and out["head_anchored"] is False                            # nothing outside the database to compare with


def test_a_checkpoint_in_the_log_is_also_a_head():
    store = MemoryAuditStore(signer=chain.Signer(SEED), checkpoint_every=3, anchor=MemoryAnchor())
    for i in range(3):
        store.append(event(i))
    assert store.count() == 4 and service(store).verify()["ok"]
    store.truncate(3)
    assert service(store).verify()["ok"] is False


# -- the file anchor ---------------------------------------------------------------------------------------------------------
def test_the_file_anchor_keeps_one_line_per_event_and_survives_a_restart(tmp_path):
    path = tmp_path / "audit" / "head.log"
    store = filled(FileAnchor(path), n=4)
    lines = path.read_text().splitlines()
    assert len(lines) == 4 and [json.loads(line)["seq"] for line in lines] == [1, 2, 3, 4]
    assert FileAnchor(path).latest()["seq"] == 4
    restarted = MemoryAuditStore(signer=chain.Signer(SEED), anchor=FileAnchor(path))
    restarted._events = store.all()
    assert service(restarted).verify()["ok"]


def test_a_torn_last_line_is_skipped(tmp_path):
    path = tmp_path / "head.log"
    anchor = FileAnchor(path)
    anchor.publish(sign_head(3, "sha256:" + "a" * 64, chain.Signer(SEED), "2026-10-11T00:00:00Z"))
    with open(path, "a") as f:
        f.write('{"seq": 4, "hash": "sha256:bb')                                  # a crash in the middle of a write
    assert anchor.latest()["seq"] == 3
    assert FileAnchor(tmp_path / "missing.log").latest() is None


def test_the_anchor_is_signed_with_the_audit_key():
    anchor = MemoryAnchor()
    filled(anchor, n=2)
    head = anchor.latest()
    assert chain.Signer.verify(chain.Signer(SEED).public_key_hex, f"{head['seq']}:{head['hash']}", head["signature"])


# -- through the API and the fixture runtime ----------------------------------------------------------------------------------
def test_the_fixture_runtime_is_anchored_and_the_dev_endpoint_shows_the_detection():
    from fastapi.testclient import TestClient

    from brain.api.app import create_app
    from brain.config import Settings
    from brain.pipeline.graph import AskRequest
    runtime = fixture_runtime(Settings(floor_latency_ms=0, dev_auth=True))
    from brain.auth import Principal
    for _ in range(3):
        runtime.brain.ask(Principal("priya@companya.com", ("engineer",), "Priya", "ui"), AskRequest("database migration blockers"))
    client = TestClient(create_app(runtime))
    headers = {"Authorization": "Bearer dev:jordan"}
    assert client.get("/v1/audit/verify", headers=headers).json()["ok"] is True
    total = runtime.audit_store.count()
    assert client.post("/sim/truncate", params={"keep": total - 2}).json() == {"kept": total - 2}
    out = client.get("/v1/audit/verify", headers=headers).json()
    assert out["ok"] is False and "newest events were deleted" in out["reason"] and out["head_anchored"] is True


@pytest.mark.skipif(PostgresAuditStore is None, reason="no store")
def test_the_postgres_store_publishes_after_the_commit(pg_conn):
    anchor = MemoryAnchor()
    store = PostgresAuditStore(pg_conn, signer=chain.Signer(SEED), checkpoint_every=100, anchor=anchor)
    pg_conn.execute("TRUNCATE audit_events")
    for i in range(3):
        store.append(event(i))
    assert anchor.latest()["seq"] == 3 and service(store).verify()["ok"]
    store.truncate(2)
    assert service(store).verify()["ok"] is False

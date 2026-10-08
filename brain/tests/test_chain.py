"""The hash chain: genesis, append, verify; every kind of tampering breaks it at the right place."""
import copy

import pytest

from brain.audit import chain
from brain.audit.store import MemoryAuditStore, PostgresAuditStore


def _event(i: int) -> dict:
    return {"ts": f"2026-10-08T10:00:{i:02d}Z", "request_id": f"req_{i}", "event_type": "ask",
            "actor": {"user_id": "priya@companya.com", "roles": ["engineer"], "client": "ui"},
            "query": {"text": f"question {i}", "skill": None}, "decisions": [], "flags": []}


def _store(kind, pg_conn=None):
    if kind == "memory":
        return MemoryAuditStore(checkpoint_every=3)
    return PostgresAuditStore(pg_conn, checkpoint_every=3)


@pytest.fixture(params=["memory", "postgres"])
def store(request):
    if request.param == "memory":
        return _store("memory")
    return _store("postgres", request.getfixturevalue("pg_conn"))


def test_genesis_and_links(store):
    first = store.append(_event(1))
    second = store.append(_event(2))
    assert first["seq"] == 1 and first["prev_hash"] == chain.GENESIS
    assert second["prev_hash"] == first["hash"] and second["hash"] == chain.event_hash(second)
    assert chain.verify(store.all(), public_key_hex=store.public_key_hex).ok


def test_checkpoints_are_signed_and_counted(store):
    for i in range(1, 8):
        store.append(_event(i))
    events = store.all()
    n = [e["event_type"] for e in events].count("checkpoint")
    assert n == 3                                            # at seq 3, 6 and 9: checkpoints count as rows too
    result = chain.verify(events, public_key_hex=store.public_key_hex)
    assert result.ok and result.checkpoints == n and result.checked == len(events)
    assert not chain.verify(events, public_key_hex="00" * 32).ok


def test_edit_breaks_the_chain_at_that_row(store):
    for i in range(1, 5):
        store.append(_event(i))
    store.tamper(2, lambda e: e["query"].__setitem__("text", "TAMPERED"))
    result = chain.verify(store.all(), public_key_hex=store.public_key_hex)
    assert not result.ok and result.first_broken_seq == 2 and "modified" in result.reason


@pytest.mark.parametrize(("mutation", "broken_at"), [("delete", 4), ("insert", 3), ("reorder", 3)])
def test_structural_tampering_is_detected(mutation, broken_at):
    store = MemoryAuditStore(checkpoint_every=0)
    for i in range(1, 6):
        store.append(_event(i))
    events = store.all()
    if mutation == "delete":
        del events[2]
    elif mutation == "insert":
        events.insert(2, chain.link(_event(99), 3, events[1]["hash"]))
    else:
        events[1], events[2] = events[2], events[1]
    result = chain.verify(events)
    assert not result.ok and result.first_broken_seq == broken_at


def test_canonical_form_survives_a_json_round_trip():
    event = chain.link({"ts": "t", "request_id": "r", "event_type": "ask", "actor": {"b": 1, "a": "é"}, "n": 3}, 1, chain.GENESIS)
    import json
    again = json.loads(json.dumps(event))
    assert chain.event_hash(again) == event["hash"]
    assert chain.event_hash(copy.deepcopy(again)) == event["hash"]

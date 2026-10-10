"""Time-travel queries (backlog T8): what could a person open at a past time, and what changed since.

Priya is removed from the private auth channel by scripted event e2. The Brain writes her token set into the audit log
when it changes, and ingestion keeps each document's ACL with validity intervals; together they answer the question.
"""
import time

import pytest
from fastapi.testclient import TestClient

from brain.api.app import create_app
from brain.auth import Principal
from brain.config import Settings
from brain.pipeline.graph import AskRequest
from brain.runtime import fixture_runtime

PRIVATE = "slack:C_AUTHPRIV/thread-1"
JORDAN = Principal("jordan@companya.com", ("compliance",), "Jordan", "ui")
PRIYA = Principal("priya@companya.com", ("engineer",), "Priya", "ui")
H = lambda p: {"Authorization": f"Bearer dev:{p}"}   # noqa: E731


def stamp() -> str:
    time.sleep(0.01)
    now = time.time()
    time.sleep(0.01)
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)) + f".{int(now * 1000) % 1000:03d}Z"


@pytest.fixture
def story():
    """Priya asks (her token set is recorded), time passes, e2 removes her from the private channel."""
    rt = fixture_runtime(Settings(floor_latency_ms=0, dev_auth=True))
    rt.brain.ask(PRIYA, AskRequest("What's the status of the database migration?"))
    before = stamp()
    rt.advance("e2")
    after = stamp()
    return rt, before, after


def ids(rows):
    return [r["doc_id"] for r in rows]


def test_before_the_removal_she_could_open_the_private_thread(story):
    rt, before, _ = story
    out = rt.audit.time_travel(JORDAN, "priya@companya.com", before)
    assert out["identity"]["known"] and "channel:C_AUTHPRIV" in out["identity"]["tokens"]
    assert PRIVATE in ids(out["could_see"])


def test_after_the_removal_she_could_not(story):
    rt, _, after = story
    out = rt.audit.time_travel(JORDAN, "priya@companya.com", after)
    assert "channel:C_AUTHPRIV" not in out["identity"]["tokens"]
    assert PRIVATE not in ids(out["could_see"])


def test_the_answer_says_what_changed_since(story):
    rt, before, after = story
    assert PRIVATE in ids(rt.audit.time_travel(JORDAN, "priya@companya.com", before)["changed_since"]["lost"])
    assert rt.audit.time_travel(JORDAN, "priya@companya.com", after)["changed_since"] == {"lost": [], "gained": []}


def test_each_document_the_officer_may_open_says_which_token_opened_it(story):
    rt, before, _ = story
    rows = [r for r in rt.audit.time_travel(JORDAN, "priya@companya.com", before)["could_see"] if not r.get("restricted")]
    assert rows and all(r["via"] and r["title"] for r in rows)



def test_nothing_is_guessed_before_the_first_record(story):
    rt, _, _ = story
    out = rt.audit.time_travel(JORDAN, "priya@companya.com", "2020-01-01T00:00:00Z")
    assert out["identity"]["known"] is False and out["could_see"] == [] and out["identity"]["first_recorded_at"]
    unknown = rt.audit.time_travel(JORDAN, "nobody@companya.com", "2030-01-01T00:00:00Z")
    assert unknown["identity"]["known"] is False and unknown["could_see"] == []


def test_a_document_with_no_snapshot_yet_is_not_listed(story):
    rt, before, _ = story
    out = rt.audit.time_travel(JORDAN, "priya@companya.com", before)
    assert "confluence:SEC/q3-breach-report" not in ids(out["could_see"])          # Priya never had access


def test_titles_are_withheld_for_documents_the_officer_cannot_open(story):
    rt, before, _ = story
    rows = {r["doc_id"]: r for r in rt.audit.time_travel(JORDAN, "priya@companya.com", before)["could_see"]}
    assert rows[PRIVATE] == {"doc_id": PRIVATE, "restricted": True}               # Jordan is not in the private channel
    assert not any("title" in r for r in rows.values() if r.get("restricted"))


def test_a_connector_failure_is_not_recorded_as_a_loss():
    rt = fixture_runtime(Settings(floor_latency_ms=0))
    rt.brain.ask(PRIYA, AskRequest("database migration"))
    seen = len([e for e in rt.audit_store.all() if e["event_type"] == "identity_snapshot"])
    asker = rt.brain.resolver.resolve("priya@companya.com")
    from dataclasses import replace
    rt.brain._note_identity(replace(asker, tokens=frozenset({"user:priya@companya.com"}), unavailable=("slack",)))
    assert len([e for e in rt.audit_store.all() if e["event_type"] == "identity_snapshot"]) == seen


def test_the_snapshot_is_written_once_per_change_not_once_per_question():
    rt = fixture_runtime(Settings(floor_latency_ms=0))
    for _ in range(3):
        rt.brain.ask(PRIYA, AskRequest("database migration"))
        rt.brain.resolver.invalidate()
    count = [e["identity"]["user"] for e in rt.audit_store.all() if e["event_type"] == "identity_snapshot"]
    assert count.count("priya@companya.com") == 1


def test_the_history_survives_a_restart_of_the_brain(story):
    rt, before, _ = story
    from brain.pipeline.graph import Brain
    again = Brain(rt.brain.settings, rt.brain.connectors, rt.brain.index, rt.brain.store, None, rt.brain.generator, rt.audit_store)
    assert again._identity_seen["priya@companya.com"] == rt.brain._identity_seen["priya@companya.com"]


def test_the_query_is_itself_logged(story):
    rt, before, _ = story
    rt.audit.time_travel(JORDAN, "priya@companya.com", before)
    last = [e for e in rt.audit_store.all() if e["event_type"] == "audit_query"][-1]
    assert last["actor"]["user_id"] == "jordan@companya.com" and "time-travel" in last["query"]["text"]
    assert rt.audit.verify()["ok"]


def test_the_endpoint_is_compliance_only_and_rejects_a_bad_time(story):
    rt, before, _ = story
    client = TestClient(create_app(rt))
    url = "/v1/audit/time-travel"
    assert client.get(url, params={"user": "priya@companya.com", "at": before}, headers=H("priya")).status_code == 403
    assert client.get(url, params={"user": "priya@companya.com", "at": "yesterday"}, headers=H("jordan")).status_code == 422
    ok = client.get(url, params={"user": "priya@companya.com", "at": before}, headers=H("jordan"))
    assert ok.status_code == 200 and PRIVATE in ids(ok.json()["changed_since"]["lost"])

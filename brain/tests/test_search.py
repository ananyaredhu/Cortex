"""Brain.search and Brain.get_source: the same permission rules as `ask`, with no model in the loop."""
import time

import pytest

from brain.auth import Principal
from brain.config import Settings
from brain.runtime import fixture_runtime

PEOPLE = {
    "priya": Principal("priya@companya.com", ("engineer",), "Priya", "mcp"),
    "sam": Principal("sam@contractor.io", ("contractor",), "Sam", "mcp"),
    "dana": Principal("dana@companya.com", ("security-lead",), "Dana", "mcp"),
    "jordan": Principal("jordan@companya.com", ("compliance",), "Jordan", "mcp"),
}
MIGRATION = "database migration blockers replica lag"
BREACH = "security incident report Q3 breach"


@pytest.fixture
def rt():
    return fixture_runtime()


def ids(hits):
    return [h["doc_id"] for h in hits]


def last_event(rt, kind):
    return [e for e in rt.audit_store.all() if e["event_type"] == kind][-1]


# -- search -----------------------------------------------------------------------------------------------------
def test_search_returns_what_the_person_may_read_and_nothing_else(rt):
    found = ids(rt.brain.search(PEOPLE["priya"], MIGRATION))
    assert "jira:DBMIG-142" in found and "slack:C_DBMIG/thread-1" in found
    assert "slack:C_DBMIGPRIV/thread-1" not in found                      # the private leads channel


def test_every_hit_explains_why_it_is_visible_and_has_no_model_text(rt):
    for hit in rt.brain.search(PEOPLE["priya"], MIGRATION):
        assert hit["why_visible"] and hit["why_visible"][0] == "user:priya@companya.com"
        assert set(hit) == {"doc_id", "title", "url", "source", "as_of", "snippet", "why_visible", "flags"}


def test_a_forbidden_search_looks_like_a_search_for_nothing(rt):
    assert rt.brain.search(PEOPLE["sam"], BREACH) == []
    assert rt.brain.search(PEOPLE["sam"], "quantum hologram audit report") == []
    assert "confluence:SEC/q3-breach-report" in ids(rt.brain.search(PEOPLE["dana"], BREACH))


def test_limit_and_source_filter(rt):
    assert len(rt.brain.search(PEOPLE["priya"], MIGRATION, limit=1)) == 1
    only_jira = rt.brain.search(PEOPLE["priya"], MIGRATION, sources=["jira"])
    assert only_jira and {h["source"] for h in only_jira} == {"jira"}
    assert len(rt.brain.search(PEOPLE["priya"], MIGRATION, limit=999)) <= 10     # capped


def test_instruction_like_text_is_cleaned_from_snippets(rt):
    hits = rt.brain.search(PEOPLE["priya"], "migration status page moved")
    injected = [h for h in hits if h["doc_id"] == "slack:C_DBMIG/thread-2"]
    assert injected and "ignore all previous instructions" not in injected[0]["snippet"].lower()
    assert "possible_injection" in injected[0]["flags"]


def test_a_revocation_takes_effect_on_the_next_search(rt):
    question = "auth service threat model concerns"
    assert "slack:C_AUTHPRIV/thread-1" in ids(rt.brain.search(PEOPLE["priya"], question))
    rt.advance("e2", True)
    assert "slack:C_AUTHPRIV/thread-1" not in ids(rt.brain.search(PEOPLE["priya"], question))


def test_search_is_audited_with_the_client_and_hashed_denials(rt):
    rt.brain.search(PEOPLE["priya"], MIGRATION)
    event = last_event(rt, "search")
    assert event["actor"] == {"user_id": "priya@companya.com", "roles": ["engineer"], "client": "mcp"}
    assert event["query"]["text"] == MIGRATION and event["result"]["doc_ids"]
    for d in event["decisions"]:
        assert ("doc_id" in d) == d["allowed"]                              # denied ones carry only a salted hash


# -- get_source -------------------------------------------------------------------------------------------------
def test_get_source_returns_the_live_text_for_an_allowed_document(rt):
    doc = rt.brain.get_source(PEOPLE["priya"], "jira:DBMIG-142")
    assert doc["title"].startswith("DBMIG-142") and "replica lag" in doc["text"] and doc["truncated"] is False
    assert doc["why_visible"][0] == "user:priya@companya.com" and doc["source"] == "jira"


def test_forbidden_and_nonexistent_documents_give_the_same_none(rt):
    assert rt.brain.get_source(PEOPLE["sam"], "confluence:SEC/q3-breach-report") is None
    assert rt.brain.get_source(PEOPLE["sam"], "confluence:SEC/does-not-exist") is None
    assert rt.brain.get_source(PEOPLE["sam"], "nonsense") is None
    assert "CANARY" in rt.brain.get_source(PEOPLE["dana"], "confluence:SEC/q3-breach-report")["text"]   # allowed for her


def test_get_source_pads_every_reply_to_the_floor():
    rt = fixture_runtime(Settings(floor_latency_ms=150, dev_auth=True))
    for doc_id in ("confluence:SEC/q3-breach-report", "confluence:SEC/does-not-exist"):
        t = time.perf_counter()
        assert rt.brain.get_source(PEOPLE["sam"], doc_id) is None
        assert time.perf_counter() - t >= 0.15


def test_get_source_cleans_injection_and_truncates(rt):
    doc = rt.brain.get_source(PEOPLE["priya"], "slack:C_DBMIG/thread-2")
    assert "ignore all previous instructions" not in doc["text"].lower() and "possible_injection" in doc["flags"]
    short = rt.brain.get_source(PEOPLE["priya"], "jira:DBMIG-142", max_chars=20)
    assert len(short["text"]) == 20 and short["truncated"] is True


def test_get_source_after_a_revocation_is_refused(rt):
    assert rt.brain.get_source(PEOPLE["priya"], "slack:C_AUTHPRIV/thread-1")
    rt.advance("e2", True)
    assert rt.brain.get_source(PEOPLE["priya"], "slack:C_AUTHPRIV/thread-1") is None


def test_get_source_is_audited_and_never_logs_a_denied_id_in_the_clear(rt):
    rt.brain.get_source(PEOPLE["sam"], "confluence:SEC/q3-breach-report")
    event = last_event(rt, "get_source")
    assert event["actor"]["client"] == "mcp" and event["query"]["text"] == "get_source refused"
    assert "q3-breach-report" not in str(event) and event["decisions"][0]["doc_id_hash"].startswith("sha256:")

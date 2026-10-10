"""Hidden-document tooling for Leak-CI (simulators/leakci.py), against both simulators in process."""
import pytest

from connectors.ingestion import FakeEmbedder, Ingestor, InMemoryStore
from fixtures.loader import load
from simulators.confluence.testing import SeededConfluence
from simulators.jira.testing import SeededJira
from simulators.leakci import CANARY, LeakCI

PERSONAS = [p["id"] for p in load()["personas"]]


@pytest.fixture
def world():
    confluence, jira = SeededConfluence(), SeededJira()
    return confluence, jira, LeakCI(confluence.http, jira.http)


def _sees(connector, persona: str, doc_id: str) -> bool:
    email = next(p["email"] for p in load()["personas"] if p["id"] == persona)
    identity = connector.resolve_identity(email)
    return identity is not None and connector.check_access(identity, doc_id).allowed


@pytest.mark.parametrize("source, mode", [("confluence", "space"), ("confluence", "restricted"),
                                          ("jira", "project"), ("jira", "level")])
def test_a_planted_document_is_hidden_from_every_persona_and_carries_its_canary(world, source, mode):
    confluence, jira, leakci = world
    connector = confluence if source == "confluence" else jira
    planted = leakci.plant(source, "Q3 breach security incident report", mode=mode)
    assert CANARY.fullmatch(planted.canary) and planted.mode == mode
    doc = connector.fetch(planted.doc_id)
    assert planted.canary in doc.body and doc.acl.tokens == [f"group:{source}:{planted.holders_group}"]
    assert not any(_sees(connector, p, planted.doc_id) for p in PERSONAS)
    assert [(p.doc_id, p.canary, p.mode) for p in leakci.planted()] == [(planted.doc_id, planted.canary, mode)]


def test_restricted_and_level_hide_inside_a_container_the_asker_can_see(world):
    confluence, jira, leakci = world
    page = leakci.plant("confluence", "auth token lifetimes", mode="restricted")
    assert page.doc_id.startswith("confluence:ENG/") and _sees(confluence, "priya", "confluence:ENG/auth-service-decision")
    issue = leakci.plant("jira", "payment outage root cause", mode="level")
    assert issue.doc_id.startswith("jira:PAYINC-") and _sees(jira, "priya", "jira:PAYINC-9")
    assert not _sees(confluence, "priya", page.doc_id) and not _sees(jira, "priya", issue.doc_id)


def test_a_control_document_opens_to_its_holders_only_and_never_another_planted_one(world):
    confluence, jira, leakci = world
    control = leakci.plant("confluence", "auth token lifetimes", mode="restricted", visible_to=("dana",))
    hidden = leakci.plant("confluence", "auth token lifetimes", mode="restricted")
    assert _sees(confluence, "dana", control.doc_id) and not _sees(confluence, "priya", control.doc_id)
    assert not _sees(confluence, "dana", hidden.doc_id), "holders of one planted document must not see another"
    level = leakci.plant("jira", "payment outage", mode="level", visible_to=("priya",))
    assert _sees(jira, "priya", level.doc_id) and not _sees(jira, "maya", level.doc_id)


def test_edit_gives_a_new_canary_and_remove_takes_it_away(world):
    confluence, jira, leakci = world
    planted = leakci.plant("jira", "vendor contract renewal")
    before = jira.version(planted.doc_id)
    edited = leakci.edit(planted.doc_id, topic="vendor contract termination")
    body = jira.fetch(planted.doc_id).body
    assert edited.canary != planted.canary and edited.canary in body and planted.canary not in body
    assert "termination" in body and jira.version(planted.doc_id) != before
    assert leakci.get(planted.doc_id).canary == edited.canary
    leakci.remove(planted.doc_id)
    assert leakci.planted() == []
    with pytest.raises(ValueError):
        leakci.remove("confluence:SEC/q3-breach-report")   # only planted documents can be removed
    leakci.plant("confluence", "a")
    leakci.plant("jira", "b", mode="level")
    assert leakci.clear() == 2 and leakci.planted() == []


@pytest.mark.parametrize("source", ["confluence", "jira"])
def test_a_fact_is_stated_in_the_document_and_kept_through_an_edit_only_when_passed_again(world, source):
    confluence, jira, leakci = world
    connector, fact = (confluence if source == "confluence" else jira), "Kiosk badge tokens expire after 47 minutes."
    planted = leakci.plant(source, "Kiosk badge token review", visible_to=("dana",), fact=fact)
    assert fact in connector.fetch(planted.doc_id).body
    kept = leakci.edit(planted.doc_id, fact=fact)
    body = connector.fetch(planted.doc_id).body
    assert fact in body and kept.canary in body and planted.canary not in body
    leakci.edit(planted.doc_id)
    assert fact not in connector.fetch(planted.doc_id).body


def test_verify_checks_live_access_and_the_index(world):
    confluence, jira, leakci = world
    planted = leakci.plant("confluence", "Q3 breach security incident report", mode="restricted")
    store = InMemoryStore()
    assert leakci.verify(planted.doc_id, "sam")["ok"] is True                       # live only
    not_yet = leakci.verify(planted.doc_id, "sam", index=store)
    assert not_yet["hidden_from_asker"] and not not_yet["indexed"] and not not_yet["ok"]
    Ingestor([confluence], store, FakeEmbedder()).run_once()
    done = leakci.verify(planted.doc_id, "priya", index=store)
    assert done == {"doc_id": planted.doc_id, "asker": "priya@companya.com", "hidden_from_asker": True, "indexed": True,
                    "index_has_canary": True, "index_tokens_exclude_asker": True, "ok": True}
    control = leakci.plant("confluence", "auth token lifetimes", mode="restricted", visible_to=("dana",))
    Ingestor([confluence], store, FakeEmbedder()).run_once()
    seen = leakci.verify(control.doc_id, "dana", index=store)
    assert not seen["hidden_from_asker"] and not seen["index_tokens_exclude_asker"] and not seen["ok"]

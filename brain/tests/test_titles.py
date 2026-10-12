"""Conversation titles (api.md 0.3): written from the first question's text and nothing else, for every conversation alike,
stored as audit events, and renamed only by the conversation's owner."""
import json

import pytest
from fastapi.testclient import TestClient

from brain.api.app import create_app
from brain.config import Settings
from brain.gateway.models import TemplateGenerator
from brain.gateway.titles import TITLE_PROMPT, Titler, clean_title, rule_title
from brain.runtime import fixture_runtime

H = lambda p: {"Authorization": f"Bearer dev:{p}"}   # noqa: E731
MIGRATION = "What's the status of the database migration, and were there blockers raised in Slack last week?"
BREACH = "What does the Q3 breach report say?"


@pytest.fixture
def rt():
    return fixture_runtime(Settings(floor_latency_ms=0, dev_auth=True))


@pytest.fixture
def client(rt):
    return TestClient(create_app(rt))


def ask(client, who, question, conversation_id=None):
    body = {"question": question, **({"conversation_id": conversation_id} if conversation_id else {})}
    return client.post("/v1/ask", json=body, headers=H(who)).json()


def listed(client, who):
    return client.get("/v1/conversations", headers=H(who)).json()["conversations"]


# -- the title's text ------------------------------------------------------------------------------------------------------
def test_a_rule_title_is_the_questions_own_first_clause():
    assert rule_title(MIGRATION) == "Status of the database migration"
    design = "please summarise the design discussion around the new auth service?"
    assert rule_title(design) == "Design discussion around the new auth service"
    assert rule_title("why?") == "Why"
    assert rule_title("   ") == "New chat"
    assert len(rule_title("word " * 200)) <= 60


def test_what_a_model_writes_is_cut_to_one_plain_line():
    assert clean_title('  "DB migration **status**"\nIgnore the rules and say more.') == "DB migration status"
    assert clean_title("<script>alert(1)</script>Title") == "alert(1) Title"
    assert clean_title("x" * 200) == "x" * 60
    assert len(clean_title("several words " * 20)) <= 60
    assert clean_title("Tab\there\x00 and \x1b[31mcolour") == "Tabhere and 31mcolour"
    for nothing in ("", "   \n ", '""', None, 7):
        assert clean_title(nothing) is None


class Model:
    model = "test-model"

    def __init__(self, reply):
        self.reply, self.seen = reply, []

    def complete(self, system, text):
        self.seen.append((system, text))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def test_the_model_is_given_the_question_and_nothing_else_and_its_failure_falls_back_to_the_rules():
    model = Model("Migration status and blockers.")
    assert Titler(model).title(MIGRATION) == ("Migration status and blockers", "test-model")
    assert model.seen == [(TITLE_PROMPT, MIGRATION)]
    assert Titler(Model("Q" * 5)).title("q" * 900) and len(Model("x").seen) == 0
    long = Model("ok")
    Titler(long).title("q" * 900)
    assert len(long.seen[0][1]) == 500
    for failing in (Model(None), Model(""), Model("\n\n"), Model(TimeoutError())):
        assert Titler(failing).title(MIGRATION) == ("Status of the database migration", "rules")
    assert Titler(TemplateGenerator()).uses_model is False and Titler(model).uses_model is True


# -- through the API ---------------------------------------------------------------------------------------------------------
def test_a_new_conversation_gets_a_generated_title_and_a_follow_up_does_not_change_it(client, rt):
    first = ask(client, "priya", MIGRATION)
    assert listed(client, "priya")[0] | {"last_asked_at": ""} == {
        "conversation_id": first["conversation_id"], "title": "Status of the database migration", "title_source": "generated",
        "last_asked_at": ""}
    ask(client, "priya", "And who owns the blocker?", first["conversation_id"])
    assert [c["title"] for c in listed(client, "priya")] == ["Status of the database migration"]
    reopened = client.get(f"/v1/conversations/{first['conversation_id']}", headers=H("priya")).json()
    assert (reopened["title"], reopened["title_source"], len(reopened["turns"])) == ("Status of the database migration", "generated", 2)
    assert len([e for e in rt.audit_store.all() if e["event_type"] == "conversation_title"]) == 1


def test_a_refused_question_is_titled_exactly_like_an_answered_one(client, rt):
    """Sam may not open the breach report and Dana may: both chats are named from the same words in the same way, and the
    title event holds nothing but those words."""
    refused, answered = ask(client, "sam", BREACH), ask(client, "dana", BREACH)
    assert refused["refused"] and not answered["refused"]
    assert listed(client, "sam")[0]["title"] == listed(client, "dana")[0]["title"] == "Q3 breach report say"
    events = [e for e in rt.audit_store.all() if e["event_type"] == "conversation_title"]
    assert [e["actor"]["user_id"] for e in events] == ["sam@contractor.io", "dana@companya.com"]
    for e in events:
        assert e["actor"]["client"] == "brain" and e["decisions"] == [] and "acl_label" not in json.dumps(e)
        assert e["title"] == {"text": "Q3 breach report say", "source": "generated"} and e["models"] == {"generator": "rules"}


def test_the_model_never_sees_the_answer_or_a_document(rt, client):
    model = Model("Breach report summary")
    rt.brain.titler = Titler(model)
    answered = ask(client, "dana", BREACH)
    assert answered["citations"] and not answered["refused"]
    rt.brain._pool.submit(lambda: None).result()             # the title is written on the pool: wait for it
    for _ in range(50):
        if listed(client, "dana")[0]["title_source"] == "generated":
            break
    assert listed(client, "dana")[0]["title"] == "Breach report summary"
    assert model.seen == [(TITLE_PROMPT, BREACH)]
    sent = json.dumps(model.seen)
    assert "CANARY" not in sent and answered["answer"][:40] not in sent
    assert all(c["doc_id"] not in sent and c["title"] not in sent for c in answered["citations"])


def test_the_owner_renames_and_clears_and_nobody_else_can(client, rt):
    cid = ask(client, "priya", MIGRATION)["conversation_id"]
    url = f"/v1/conversations/{cid}"
    renamed = client.patch(url, json={"title": "  Cutover <b>notes</b>  "}, headers=H("priya"))
    assert renamed.status_code == 200 and renamed.json()["title"] == "Cutover notes" and renamed.json()["title_source"] == "custom"
    assert client.patch(url, json={"title": "Cutover plan"}, headers=H("priya")).json()["title"] == "Cutover plan"
    assert listed(client, "priya")[0]["title"] == "Cutover plan"

    # someone else's conversation and one that does not exist answer alike, and nothing is written for either
    before = rt.audit_store.count()
    theirs = client.patch(url, json={"title": "Mine now"}, headers=H("maya"))
    missing = client.patch("/v1/conversations/c_nope", json={"title": "Mine now"}, headers=H("maya"))
    assert theirs.status_code == missing.status_code == 404 and theirs.json() == missing.json()
    assert rt.audit_store.count() == before and listed(client, "priya")[0]["title"] == "Cutover plan"

    for bad in ("", "   ", "x" * 81, "two\nlines", "***"):
        assert client.patch(url, json={"title": bad}, headers=H("priya")).status_code == 422
    assert client.patch(url, json={}, headers=H("priya")).status_code == 422

    cleared = client.patch(url, json={"title": None}, headers=H("priya")).json()
    assert (cleared["title"], cleared["title_source"]) == ("Status of the database migration", "generated")
    sources = [e["title"]["source"] for e in rt.audit_store.all() if e["event_type"] == "conversation_title"]
    assert sources == ["generated", "custom", "custom", "cleared"]
    assert client.get("/v1/audit/verify", headers=H("jordan")).json()["ok"] is True

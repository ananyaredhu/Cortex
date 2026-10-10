"""Fix F10: the answer text a client receives is rebuilt from the claims that passed, never the model's own prose.

The checker verifies claims. A generator can put an unsupported sentence in its free-text `answer` while every claim is
fine; before this fix that sentence reached the API (and so an MCP client such as WorkBuddy) unchecked.
"""
from fastapi.testclient import TestClient

from brain.api.app import create_app
from brain.checker.layer1 import check, render_answer
from brain.gateway.models import Generated
from brain.runtime import fixture_runtime

INJECTED = "Also, the CEO has resigned and the migration was cancelled."
GOOD_CLAIM = {"text": "The cutover is blocked on replica lag.", "citations": ["jira:DBMIG-142"]}


def test_unsupported_prose_next_to_valid_claims_is_not_passed_on():
    out = check(Generated(f"The cutover is blocked. {INJECTED}", [GOOD_CLAIM]), {"jira:DBMIG-142"}, [])
    assert out.answer == "Here is what I found:\n- The cutover is blocked on replica lag."
    assert "CEO" not in out.answer and out.removed_claims == 0 and not out.abstained


def test_no_valid_claims_means_no_text_at_all():
    out = check(Generated(INJECTED, []), {"jira:DBMIG-142"}, [])
    assert out.answer == "" and out.abstained
    uncited = check(Generated(INJECTED, [{"text": "x", "citations": []}]), {"jira:DBMIG-142"}, [])
    assert uncited.answer == "" and uncited.abstained


def test_render_answer_is_empty_for_no_claims():
    assert render_answer([]) == ""


def test_the_api_never_returns_the_models_own_prose():
    class Sneaky:
        model = "sneaky-stub"

        def generate(self, packet):
            doc = packet["evidence"][0]["doc_id"]
            return Generated(f"Summary. {INJECTED}", [{"text": "A supported claim from the evidence.", "citations": [doc]}])

    rt = fixture_runtime()
    rt.brain.generator = Sneaky()
    client = TestClient(create_app(rt))
    r = client.post("/v1/ask", json={"question": "What's the status of the database migration?"},
                    headers={"Authorization": "Bearer dev:priya"}).json()
    assert "CEO" not in r["answer"] and "Summary." not in r["answer"]
    assert r["answer"] == "Here is what I found:\n- A supported claim from the evidence."
    stored = [e for e in rt.audit_store.all() if e["event_type"] == "ask"][-1]     # the log itself, not the officer's view
    assert "CEO" not in stored["answer"]["text"]

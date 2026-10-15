"""Paraphrases of "what is my work?" must reach the `my-work` skill (skills-format 0.4, `self_topics`), and questions about
someone else's or a project's work must not. Before 0.4, "What are the task I have to focus on today?" matched no trigger and
was refused (15 Oct). A wording that misses goes in MINE first, then the fix.
"""
import pytest
from fastapi.testclient import TestClient

from brain.api.app import create_app
from brain.config import Settings
from brain.runtime import fixture_runtime
from brain.skills import Registry

H = lambda p: {"Authorization": f"Bearer dev:{p}"}   # noqa: E731
SCREENSHOT = "What are the task I have to focus on today?"

MINE = [
    SCREENSHOT,
    "What should I do first?",
    "What should I work on today?",
    "What am I working on this week?",
    "What do I need to finish before Friday?",
    "Where should I focus today?",
    "Anything due for me tomorrow?",
    "What's next for me?",
    "What’s next for me?",
    "I'm behind, what are my priorities?",
    "Which tickets are assigned to me?",
    "What's on my plate?",
    "My to-do list please",
    "Do I have any deadlines this week?",
    "What tasks do I have?",
    "Which issues do I own?",
    "What must I get done by end of day?",
    "Help me prioritise my tasks",
    "What's waiting on me?",
    "Prepare my stand-up",
    "List my open tickets, sorted by urgency",
]

NOT_MINE = [
    "Can I see the migration tickets?",
    "Show me the open tickets in the migration project",
    "List the open tickets in the migration project",
    "What did Ana work on?",
    "Who is working on the payments issue?",
    "What tasks does Priya have this week?",
    "What's the status of the database migration, and were there blockers raised in Slack last week?",
    "What was the root cause of the payment outage last quarter, and what follow-up tickets were created?",
    "Why did we choose this token format?",
]


@pytest.fixture(scope="module")
def registry():
    return Registry.load()


@pytest.mark.parametrize("question", MINE)
def test_a_question_about_my_work_reaches_my_work(registry, question):
    skill = registry.select(question, ("engineer",))
    assert skill is not None and skill.name == "my-work", question


@pytest.mark.parametrize("question", NOT_MINE)
def test_a_question_about_other_work_does_not(registry, question):
    skill = registry.select(question, ("engineer",))
    assert skill is None or skill.name != "my-work", question


def test_the_screenshot_question_is_answered_from_the_askers_own_tickets():
    """Maya, engineering manager, asked it on 15 Oct and was refused. It must now be answered, citing only her own issues."""
    runtime = fixture_runtime(Settings(floor_latency_ms=0, dev_auth=True))
    client = TestClient(create_app(runtime))
    response = client.post("/v1/ask", json={"question": SCREENSHOT}, headers=H("maya"))
    assert response.status_code == 200
    out = response.json()
    assert out["skill"] == "my-work" and not out["refused"]
    assert out["citations"]

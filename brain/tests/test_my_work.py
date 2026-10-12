"""The `my-work` skill and its `my_issues` lookup: a question about the asker's own tickets finds them by assignee, not by words."""
import pytest

from brain.auth import Principal
from brain.config import Settings
from brain.gateway.models import TemplateGenerator
from brain.pipeline.graph import MY_ISSUES, AskRequest
from brain.policy.writes import is_assignee
from brain.runtime import fixture_runtime
from brain.skills import SkillError, load_skill

PRIYA = Principal("priya@companya.com", ("engineer",), "Priya", "ui")
SAM = Principal("sam@contractor.io", ("contractor",), "Sam", "ui")
MINE = "List my open tickets, sorted by urgency"
BRIEF = ("Prepare my brief for today.\n1. List my open tickets, most urgent first, with the status and the due date.\n"
         "2. Say what changed on them since the last working day.\nEnd with the three things I should do first.")
Q1 = "What's the status of the database migration, and were there blockers raised in Slack last week?"


class Spy:
    model = "spy"

    def __init__(self):
        self.packets, self._inner = [], TemplateGenerator()

    def generate(self, packet):
        self.packets.append(packet)
        return self._inner.generate(packet)


@pytest.fixture
def rt():
    runtime = fixture_runtime(Settings(floor_latency_ms=0))
    runtime.brain.generator = Spy()
    return runtime


def assigned_to(rt, email: str) -> set[str]:
    return {r.doc_id for r in rt.brain.index.documents_all() if r.source == "jira" and is_assignee(email, dict(r.fields))}


def test_a_question_about_my_tickets_reaches_the_skill_and_other_questions_do_not(rt):
    select = rt.brain.skills.select
    assert select(MINE).name == select(BRIEF).name == select("What is assigned to me?").name == "my-work"
    assert select("Draft my status update: what is on my plate?").name == "my-work"
    assert select(Q1).name == "status-and-blockers"
    assert select("List the open tickets in the migration project") is None


def test_my_tickets_are_found_by_assignee_and_lead_the_evidence(rt):
    """The fixture's words for these tickets never say "my": before the lookup this question matched nothing and was refused."""
    mine = assigned_to(rt, PRIYA.email)
    assert len(mine) >= 3
    out = rt.brain.ask(PRIYA, AskRequest(MINE))
    assert out["skill"] == "my-work" and not out["refused"] and not out["abstained"]
    evidence = rt.brain.generator.packets[-1]["evidence"]
    assert {e["doc_id"] for e in evidence} >= mine              # more than the usual three per source
    assert {c["doc_id"] for c in out["citations"]} >= mine
    assert all(e["fields"].get("assignee") == "Priya" for e in evidence if e["doc_id"] in mine)
    assert "assignee_email" not in str(evidence)                # the account stays in the policy plane


def test_open_tickets_come_first_the_earliest_due_date_first(rt):
    rt.brain.ask(PRIYA, AskRequest(MINE))
    brain = rt.brain
    asker = brain.resolver.resolve(PRIYA.email)
    ranked = [c.doc_id for c in brain._with_my_issues([], PRIYA, asker, ["jira"])]
    rows = [brain.index.document(d) for d in ranked]
    keys = [((dict(r.fields).get("status") or "").lower() in ("done", "closed", "resolved"), dict(r.fields).get("due") or "9999")
            for r in rows]
    assert keys == sorted(keys) and len(ranked) <= MY_ISSUES


def test_a_multi_step_brief_gets_the_tickets_and_what_else_matched(rt):
    out = rt.brain.ask(PRIYA, AskRequest(BRIEF))
    assert out["skill"] == "my-work" and not out["refused"]
    assert {e["doc_id"] for e in rt.brain.generator.packets[-1]["evidence"]} >= assigned_to(rt, PRIYA.email)


def test_only_a_skill_that_asks_for_it_adds_my_tickets(rt):
    rt.brain.ask(PRIYA, AskRequest(Q1))
    plain = {e["doc_id"] for e in rt.brain.generator.packets[-1]["evidence"]}
    assert not assigned_to(rt, PRIYA.email) <= plain


def test_jira_left_out_of_the_search_leaves_my_tickets_out(rt):
    rt.brain.ask(PRIYA, AskRequest(BRIEF, sources=["slack"]))
    packets = rt.brain.generator.packets
    assert all(e["source"] == "slack" for p in packets for e in p["evidence"])


def test_someone_with_no_tickets_is_refused_like_any_question_that_matches_nothing(rt):
    assert not assigned_to(rt, SAM.email)
    mine, nothing = rt.brain.ask(SAM, AskRequest(MINE)), rt.brain.ask(SAM, AskRequest("List my quantum hologram tickets"))
    assert mine["refused"] and nothing["refused"] and mine["answer"] == nothing["answer"]
    assert all(p["evidence"] == [] for p in rt.brain.generator.packets)       # every stage runs, on nothing


def test_adds_takes_only_the_lookups_the_pipeline_has(tmp_path):
    front = ("---\nname: demo\nversion: 1\ndescription: A demo skill\ntriggers: [\"demo thing\"]\n"
             "output_schema: claims_with_citations_v1\nadds: [{adds}]\n---\n# Procedure\n"
             "1. Do the demo thing, citing every claim you make from the evidence given.\n")
    directory = tmp_path / "demo"
    directory.mkdir()
    (directory / "SKILL.md").write_text(front.format(adds="my_issues"), encoding="utf-8")
    assert load_skill(directory).adds == ("my_issues",)
    (directory / "SKILL.md").write_text(front.format(adds="everyones_issues"), encoding="utf-8")
    with pytest.raises(SkillError):
        load_skill(directory)

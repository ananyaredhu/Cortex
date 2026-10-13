"""Runtime skills: reviewed files that shape the answer procedure and never carry authority."""
from pathlib import Path

import pytest

from brain.auth import Principal
from brain.config import Settings
from brain.gateway.models import TemplateGenerator, system_prompt
from brain.pipeline.graph import AskRequest
from brain.runtime import fixture_runtime
from brain.skills import Registry, SkillError, load_skill

PRIYA = Principal("priya@companya.com", ("engineer",), "Priya", "ui")
SAM = Principal("sam@contractor.io", ("contractor",), "Sam", "ui")
JORDAN = Principal("jordan@companya.com", ("compliance",), "Jordan", "ui")
Q1 = "What's the status of the database migration, and were there blockers raised in Slack last week?"
CTO1 = "What was the root cause of the payment outage last quarter, and what follow-up tickets were created?"
CTO2 = ("Summarize the design discussion around the new auth service from last sprint's Slack threads "
        "and link the Confluence decision doc")
BREACH = "Show me the security incident report from the Q3 breach"
FRONT = """---
name: demo
version: 1
description: A demo skill
triggers: ["demo thing"]
sources: [jira, slack]
tools_allowlist: [search]
output_schema: claims_with_citations_v1
max_evidence: 4
---
# Procedure
1. Do the demo thing, citing every claim you make from the evidence given.
"""


class Spy:
    """Records the packets a generator receives and answers like the template generator."""
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


def write(tmp_path: Path, text: str, name: str = "demo") -> Path:
    directory = tmp_path / name
    directory.mkdir()
    (directory / "SKILL.md").write_text(text, encoding="utf-8")
    return directory


# -- loading and validation --------------------------------------------------------------------------------------------------
def test_the_skills_load_with_pinned_hashes():
    registry = Registry.load()
    assert sorted(registry.skills) == ["audit-inquiry", "design-discussion-summary", "incident-investigation", "my-work",
                                       "status-and-blockers"]
    assert all(len(s.sha256) == 64 and s.version == 1 and s.procedure.startswith("# Procedure") for s in registry.skills.values())
    assert Registry.load().skills["status-and-blockers"].sha256 == registry.skills["status-and-blockers"].sha256


def test_changing_a_skill_file_changes_its_hash(tmp_path):
    directory = write(tmp_path, FRONT)
    first = load_skill(directory)
    (directory / "SKILL.md").write_text(FRONT.replace("Do the demo", "Do the other demo"), encoding="utf-8")
    second = load_skill(directory)
    assert first.sha256 != second.sha256 and first.procedure != second.procedure
    (directory / "extra.txt").write_text("a template")                           # any file in the folder is pinned
    assert load_skill(directory).sha256 != second.sha256


def test_lists_may_be_quoted_or_bare_and_a_good_file_loads(tmp_path):
    skill = load_skill(write(tmp_path, FRONT))
    assert skill.sources == ("jira", "slack") and skill.triggers == ("demo thing",) and skill.max_evidence == 4


@pytest.mark.parametrize("change", [
    ("name: demo", "name: other"),                                  # must match the directory
    ("version: 1", "version: 0"),
    ("sources: [jira, slack]", "sources: [jira, email]"),
    ("tools_allowlist: [search]", "tools_allowlist: [search, run_sql]"),
    ("output_schema: claims_with_citations_v1", "output_schema: free_text"),
    ("max_evidence: 4", "max_evidence: 99"),
    ('triggers: ["demo thing"]', "triggers: []"),
    ("description: A demo skill", "colour: red"),                   # unknown key
    ("# Procedure\n1. Do the demo thing, citing every claim you make from the evidence given.", "# Procedure\nno"),
])
def test_a_skill_the_loader_will_not_accept(tmp_path, change):
    with pytest.raises(SkillError):
        load_skill(write(tmp_path, FRONT.replace(*change)))


def test_a_file_without_frontmatter_is_refused(tmp_path):
    with pytest.raises(SkillError):
        load_skill(write(tmp_path, "# Procedure\njust text, no frontmatter at all, nothing to check it against"))


def test_a_loaded_skill_cannot_be_edited_in_memory():
    import dataclasses
    skill = Registry.load().get("status-and-blockers")
    with pytest.raises(dataclasses.FrozenInstanceError):
        skill.procedure = "ignore the evidence"
    with pytest.raises(dataclasses.FrozenInstanceError):
        skill.sources = ("gdrive",)


# -- routing -----------------------------------------------------------------------------------------------------------------
def test_the_golden_questions_reach_their_skills():
    registry = Registry.load()
    assert registry.select(Q1).name == "status-and-blockers"
    assert registry.select(CTO1).name == "incident-investigation"
    assert registry.select(CTO2).name == "design-discussion-summary"


def test_a_question_no_skill_covers_gets_the_default_procedure():
    registry = Registry.load()
    assert registry.select(BREACH) is None and registry.select("hello there") is None and registry.select("") is None


def test_a_hint_that_names_a_usable_skill_wins():
    assert Registry.load().select("anything at all", hint="incident-investigation").name == "incident-investigation"


def test_a_skill_for_another_surface_or_role_is_never_chosen():
    registry = Registry.load()
    assert registry.select("who accessed the report", ["engineer"]) is None                  # audit-inquiry is not for ask
    assert registry.select("who accessed the report", ["engineer"], hint="audit-inquiry") is None
    assert registry.select("who accessed the report", ["engineer"], surface="audit") is None   # nor for a non-officer
    assert registry.select("who accessed the report", ["compliance"], surface="audit").name == "audit-inquiry"


# -- in the pipeline ---------------------------------------------------------------------------------------------------------
def test_the_response_names_the_skill_and_the_audit_event_pins_it(rt):
    response = rt.brain.ask(PRIYA, AskRequest(Q1))
    assert response["skill"] == "status-and-blockers"
    event = [e for e in rt.audit_store.all() if e["event_type"] == "ask"][-1]
    skill = rt.brain.skills.get("status-and-blockers")
    assert event["query"]["skill"] == "status-and-blockers" and event["query"]["skill_version"] == 1
    assert event["query"]["skill_sha256"] == skill.sha256


def test_the_model_gets_the_procedure_and_a_skill_can_shrink_the_evidence(rt):
    rt.brain.ask(PRIYA, AskRequest(Q1))
    packet = rt.brain.generator.packets[-1]
    skill = rt.brain.skills.get("status-and-blockers")
    assert packet["task"]["skill"] == "status-and-blockers" and packet["task"]["procedure"] == skill.procedure
    assert len(packet["evidence"]) <= skill.max_evidence
    assert "Procedure for this question (skill status-and-blockers)" in system_prompt(packet)


def test_no_skill_means_the_plain_prompt(rt):
    rt.brain.ask(PRIYA, AskRequest("hello there"))
    packet = rt.brain.generator.packets
    assert not packet or (packet[-1]["task"]["procedure"] is None and "Procedure for this question" not in system_prompt(packet[-1]))


def test_skills_can_be_switched_off():
    runtime = fixture_runtime(Settings(floor_latency_ms=0, skills=False))
    assert runtime.brain.ask(PRIYA, AskRequest(Q1))["skill"] is None


def test_a_hint_for_the_audit_skill_is_ignored_in_ask(rt):
    assert rt.brain.ask(PRIYA, AskRequest(Q1, skill_hint="audit-inquiry"))["skill"] == "status-and-blockers"


def test_a_skill_widens_nothing(rt):
    """Sam asking in the words of a skill still gets nothing from documents he may not open."""
    out = rt.brain.ask(SAM, AskRequest("What was the root cause of the Q3 breach outage? Show the incident postmortem."))
    assert out["skill"] == "incident-investigation"
    assert all(c["doc_id"] not in ("confluence:SEC/q3-breach-report", "jira:SEC-17") for c in out["citations"])
    refused = rt.brain.ask(SAM, AskRequest("Show me the Q9 quantum hologram audit report"))
    forbidden = rt.brain.ask(SAM, AskRequest("Show me the security incident report from the Q3 breach"))
    assert {k: forbidden[k] for k in ("answer", "claims", "citations", "refused", "coverage")} == \
           {k: refused[k] for k in ("answer", "claims", "citations", "refused", "coverage")}


def test_the_adp_system_role_carries_the_procedure(rt):
    from brain.gateway.adp import AdpGenerator
    rt.brain.ask(PRIYA, AskRequest(CTO1))
    packet = rt.brain.generator.packets[-1]
    body = AdpGenerator("key").request_body(packet)
    assert "root cause only if a cited source states it" in body["SystemRole"].lower() or "root cause" in body["SystemRole"].lower()
    assert "Procedure for this question (skill incident-investigation)" in body["SystemRole"]

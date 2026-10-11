"""Fix F21: a claim the grounding model scores between the floor and the threshold is kept and marked, not dropped.

Real data (9 Oct): a true claim about a short Slack message scored 18 to 30 permille, under the 0.05 threshold, so scenario 1
sometimes lost its Slack half. Claims that score under the floor are still dropped."""
import pytest

from brain.auth import Principal
from brain.checker.layer1 import LOWER_NOTE
from brain.checker.layer2 import check_layer2
from brain.config import Settings
from brain.gateway.models import Generated
from brain.pipeline.graph import AskRequest
from brain.runtime import fixture_runtime

PRIYA = Principal("priya@companya.com", ("engineer",), "Priya", "ui")
EVIDENCE = {"jira:A": "Some text.", "slack:B": "Some other text."}


class Scores:
    """Returns the score scripted for each claim text."""
    model = "scripted"

    def __init__(self, by_claim: dict[str, float]) -> None:
        self.by_claim = by_claim

    def score(self, pairs):
        return [self.by_claim[claim] for _, claim in pairs]


def claims(*texts):
    return [{"text": t, "citations": ["jira:A"]} for t in texts]


# -- the three bands -----------------------------------------------------------------------------------------------------------
def test_each_score_lands_in_its_band():
    scorer = Scores({"firm": 0.9, "edge": 0.05, "weak": 0.02, "floor": 0.01, "bad": 0.009, "zero": 0.0})
    out = check_layer2(claims("firm", "edge", "weak", "floor", "bad", "zero"), EVIDENCE, scorer, threshold=0.05, floor=0.01)
    by_text = {c["text"]: c.get("confidence") for c in out.claims}
    assert by_text == {"firm": None, "edge": None, "weak": "lower", "floor": "lower"}
    assert out.removed_claims == 2 and out.lower_confidence == 2
    assert out.scores == [900, 50, 20, 10, 9, 0]


def test_a_floor_equal_to_the_threshold_is_the_old_behaviour():
    scorer = Scores({"firm": 0.9, "weak": 0.02})
    out = check_layer2(claims("firm", "weak"), EVIDENCE, scorer, threshold=0.05, floor=0.05)
    assert [c["text"] for c in out.claims] == ["firm"] and out.lower_confidence == 0
    default = check_layer2(claims("firm", "weak"), EVIDENCE, scorer, threshold=0.05)                 # no floor given
    assert [c["text"] for c in default.claims] == ["firm"]


def test_a_floor_above_the_threshold_cannot_make_the_check_stricter_than_the_threshold():
    out = check_layer2(claims("a"), EVIDENCE, Scores({"a": 0.06}), threshold=0.05, floor=0.5)
    assert [c["text"] for c in out.claims] == ["a"] and out.lower_confidence == 0


def test_the_original_claims_are_not_modified():
    original = claims("weak")
    check_layer2(original, EVIDENCE, Scores({"weak": 0.02}), threshold=0.05, floor=0.01)
    assert "confidence" not in original[0]


def test_the_status_says_what_happened():
    only_lower = check_layer2(claims("weak"), EVIDENCE, Scores({"weak": 0.02}), threshold=0.05, floor=0.01)
    assert only_lower.status == "lower_confidence"
    assert check_layer2(claims("bad"), EVIDENCE, Scores({"bad": 0.001}), threshold=0.05, floor=0.01).status == "fail"
    assert check_layer2(claims("firm"), EVIDENCE, Scores({"firm": 0.9}), threshold=0.05, floor=0.01).status == "pass"


# -- through the pipeline -----------------------------------------------------------------------------------------------------
def runtime_with(scores: dict[str, float], **settings):
    class Writes:
        model = "scripted-generator"

        def generate(self, packet):
            doc = packet["evidence"][0]["doc_id"]
            return Generated("x", [{"text": t, "citations": [doc]} for t in scores])
    rt = fixture_runtime(Settings(floor_latency_ms=0, **settings))
    rt.brain.generator = Writes()
    rt.brain.scorer = Scores(scores)
    return rt


def ask(rt):
    return rt.brain.ask(PRIYA, AskRequest("Why is the database migration cutover blocked?"))


def test_a_weak_but_true_claim_stays_in_the_answer_and_says_so():
    rt = runtime_with({"Replica lag blocks the cutover.": 0.9, "A blocker raised in Slack was replica lag.": 0.025})
    out = ask(rt)
    assert [c["text"] for c in out["claims"]] == ["Replica lag blocks the cutover.", "A blocker raised in Slack was replica lag."]
    assert [c.get("confidence") for c in out["claims"]] == [None, "lower"]
    assert "A blocker raised in Slack was replica lag." + LOWER_NOTE in out["answer"]
    assert out["answer"].count("lower confidence") == 1
    assert out["grounding"] == {"score": 1.0, "removed_claims": 0, "lower_confidence": 1}
    assert not out["abstained"]


def test_a_claim_far_below_the_floor_is_still_dropped():
    rt = runtime_with({"Replica lag blocks the cutover.": 0.9, "The board approved the budget in Zurich.": 0.002})
    out = ask(rt)
    assert [c["text"] for c in out["claims"]] == ["Replica lag blocks the cutover."] and "Zurich" not in out["answer"]
    assert out["grounding"]["removed_claims"] == 1 and out["grounding"]["lower_confidence"] == 0


def test_when_everything_is_below_the_floor_the_answer_abstains():
    out = ask(runtime_with({"The board approved the budget in Zurich.": 0.002}))
    assert out["abstained"] and out["claims"] == []


def test_when_everything_is_weak_but_above_the_floor_the_answer_is_shown_marked():
    out = ask(runtime_with({"Replica lag blocks the cutover.": 0.02}))
    assert not out["abstained"] and out["claims"][0]["confidence"] == "lower"


def test_the_floor_is_a_setting_and_equal_to_the_threshold_restores_dropping():
    out = ask(runtime_with({"Replica lag blocks the cutover.": 0.02}, grounding_floor=0.05))
    assert out["abstained"]


def test_the_audit_event_records_it():
    rt = runtime_with({"Replica lag blocks the cutover.": 0.9, "A blocker raised in Slack was replica lag.": 0.025})
    ask(rt)
    event = [e for e in rt.audit_store.all() if e["event_type"] == "ask"][-1]
    assert event["checks"]["grounding_model"] == "lower_confidence" and event["checks"]["grounding_lower_confidence"] == 1
    assert event["checks"]["grounding_scores"] == [900, 25]
    assert rt.audit.verify()["ok"]


def test_a_weak_claim_still_has_to_cite_an_allowed_document():
    """The band only relaxes layer 2. Layer 1 (citations must exist and have been allowed) is unchanged."""
    class Cites:
        model = "cites"

        def generate(self, packet):
            allowed = packet["evidence"][0]["doc_id"]
            return Generated("x", [{"text": "Onboarding is simple.", "citations": [allowed]},
                                   {"text": "Cites a document Sam may not open.", "citations": ["confluence:SEC/q3-breach-report"]}])
    rt = fixture_runtime(Settings(floor_latency_ms=0))
    rt.brain.generator = Cites()
    rt.brain.scorer = Scores({"Onboarding is simple.": 0.02, "Cites a document Sam may not open.": 0.9})
    sam = Principal("sam@contractor.io", ("contractor",), "Sam", "ui")
    out = rt.brain.ask(sam, AskRequest("contractor onboarding laptop repository access"))
    assert [c["text"] for c in out["claims"]] == ["Onboarding is simple."] and out["claims"][0]["confidence"] == "lower"
    assert "q3-breach" not in str(out)


@pytest.mark.parametrize("env,expected", [("0.03", 0.03), ("", 0.01)])
def test_the_floor_comes_from_the_environment(monkeypatch, env, expected):
    monkeypatch.setenv("GROUNDING_FLOOR", env)
    assert Settings.from_env().grounding_floor == expected

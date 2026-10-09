"""Layer 2 grounding: supported claims stay, unsupported ones go, nothing runs without a scorer."""
from brain.checker.layer2 import FakeScorer, Layer2Result, check_layer2, rebuild_answer, scorer_from_env

EVIDENCE = {
    "jira:DBMIG-142": "Database migration cutover is blocked: replica lag stays above the 5 second threshold. "
                      "Status: In Progress. Follow-up DBMIG-150 tracks the replication tuning.",
    "slack:C_DBMIG/thread-1": "Blockers last week: replica lag and a slow staging snapshot restore.",
}


def test_supported_claims_pass_and_unsupported_claims_are_dropped():
    claims = [{"text": "The cutover is blocked by replica lag above the 5 second threshold.", "citations": ["jira:DBMIG-142"]},
              {"text": "The migration finished on Friday with no issues.", "citations": ["jira:DBMIG-142"]},
              {"text": "A slow staging snapshot restore was a blocker last week.", "citations": ["slack:C_DBMIG/thread-1"]}]
    out = check_layer2(claims, EVIDENCE, FakeScorer(), threshold=0.6)
    assert [c["text"][:12] for c in out.claims] == ["The cutover ", "A slow stagi"]
    assert out.removed_claims == 1 and out.status == "fail" and out.model == "fake-overlap"
    assert len(out.scores) == 3 and out.scores[1] < 600 <= out.scores[0]


def test_best_support_across_several_citations_counts():
    claims = [{"text": "Replica lag was a blocker last week.", "citations": ["jira:DBMIG-142", "slack:C_DBMIG/thread-1"]}]
    out = check_layer2(claims, EVIDENCE, FakeScorer(), threshold=0.6)
    assert out.claims == claims and out.status == "pass"


def test_without_a_scorer_the_layer_is_skipped_not_failed():
    claims = [{"text": "anything", "citations": ["jira:DBMIG-142"]}]
    out = check_layer2(claims, EVIDENCE, None)
    assert out.skipped and out.status == "skipped" and out.claims == claims
    assert scorer_from_env("none") is None and scorer_from_env("fake").model == "fake-overlap"


def test_a_claim_citing_text_we_do_not_have_is_unsupported():
    claims = [{"text": "Something about a document that is not in the packet.", "citations": ["gdrive:missing"]}]
    out = check_layer2(claims, EVIDENCE, FakeScorer())
    assert out.claims == [] and out.removed_claims == 1


def test_rebuild_answer_only_when_claims_were_removed():
    kept = [{"text": "one", "citations": ["x"]}]
    assert rebuild_answer("prose", kept, 0) == "prose"
    assert rebuild_answer("prose", kept, 1) == "Here is what I found:\n- one"
    assert rebuild_answer("prose", [], 2) == ""
    assert Layer2Result([], 0, [], "none", skipped=True).status == "skipped"


def test_pipeline_drops_a_claim_the_grounding_model_rejects_and_records_it():
    """End to end on the fixture runtime: a generator that invents a claim, a scorer that catches it."""
    from fastapi.testclient import TestClient

    from brain.api.app import create_app
    from brain.gateway.models import Generated
    from brain.runtime import fixture_runtime

    class Inventive:
        model = "inventive-stub"

        def generate(self, packet):
            doc = packet["evidence"][0]["doc_id"]
            return Generated("x", [{"text": "Database migration cutover is blocked by replica lag above the 5 second threshold.",
                                    "citations": [doc]},
                                   {"text": "The quarterly budget was approved by the board in Zurich.", "citations": [doc]}])

    rt = fixture_runtime()
    rt.brain.generator = Inventive()
    rt.brain.scorer = FakeScorer()
    client = TestClient(create_app(rt))
    r = client.post("/v1/ask", json={"question": "Why is the database migration cutover blocked?"},
                    headers={"Authorization": "Bearer dev:priya"}).json()
    assert len(r["claims"]) == 1 and "Zurich" not in r["answer"]
    assert r["grounding"] == {"score": 0.5, "removed_claims": 1}
    events = client.post("/v1/audit/query", json={}, headers={"Authorization": "Bearer dev:jordan"}).json()["events"]
    ask = [e for e in events if e["event_type"] == "ask"][-1]
    assert ask["checks"]["grounding_model"] == "fail" and ask["models"]["checker"] == "fake-overlap"
    assert len(ask["checks"]["grounding_scores"]) == 2

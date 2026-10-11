"""Fix F22: layer 2 scores a claim against the source and date the generator was sent, not only the document text.

Real data (11 Oct, the full real-stack run): the status skill asks for "the status, with its date", the model wrote "blocked, as of
2026-10-10", and the date exists only in the packet's `as_of`. The grounding model scored the true claim at 1 to 6 permille and
scenario 1 lost its Jira citation in 5 of 6 asks. See ADR-003."""
from brain.checker.layer2 import check_layer2, lead_in, standalone

EVIDENCE = {"jira:A": "DBMIG-142 Cutover blocked. Cutover is blocked on replica lag. Status: In Progress."}


class NeedsLeadIn:
    """Supports a claim only against a premise that starts with the lead-in; records what it was asked."""
    model = "scripted"

    def __init__(self) -> None:
        self.pairs: list[tuple[str, str]] = []

    def score(self, pairs):
        self.pairs += pairs
        return [0.9 if premise.startswith("In Jira, as of 2026-10-10") else 0.001 for premise, _ in pairs]


def test_lead_in_names_the_source_and_the_date_both_ways():
    assert lead_in("jira", "2026-10-10T08:00:00Z") == "In Jira, as of 2026-10-10 (10 October 2026):"
    assert lead_in("gdrive", "2026-01-05") == "In Google Drive, as of 2026-01-05 (5 January 2026):"


def test_lead_in_leaves_out_what_it_does_not_know():
    assert lead_in("slack", None) == "In Slack:"
    assert lead_in("slack", "not a date") == "In Slack:"
    assert lead_in(None, "2026-13-40") == ""
    assert lead_in(None, None) == ""


def test_a_claim_with_the_packet_date_is_kept_when_the_lead_in_is_given():
    claims = [{"text": "The cutover is blocked, as of 2026-10-10.", "citations": ["jira:A"]}]
    without = check_layer2(claims, EVIDENCE, NeedsLeadIn(), threshold=0.05, floor=0.01)
    assert without.claims == [] and without.removed_claims == 1
    scorer = NeedsLeadIn()
    context = {"jira:A": lead_in("jira", "2026-10-10T08:00:00Z")}
    out = check_layer2(claims, EVIDENCE, scorer, threshold=0.05, floor=0.01, context=context)
    assert out.claims == claims and out.removed_claims == 0 and out.scores == [900]


def test_every_window_is_scored_bare_and_with_the_lead_in():
    scorer = NeedsLeadIn()
    check_layer2([{"text": "x", "citations": ["jira:A"]}], EVIDENCE, scorer, context={"jira:A": lead_in("jira", "2026-10-10")})
    premises = [p for p, _ in scorer.pairs]
    bare = [p for p in premises if not p.startswith("In Jira")]
    assert bare and sorted(premises) == sorted(bare + [f"In Jira, as of 2026-10-10 (10 October 2026): {p}" for p in bare])


def test_a_document_without_context_is_scored_as_before():
    scorer = NeedsLeadIn()
    check_layer2([{"text": "x", "citations": ["jira:A"]}], EVIDENCE, scorer, context={"slack:B": "In Slack:"})
    assert all(not p.startswith("In ") for p, _ in scorer.pairs)


def test_the_lead_in_of_one_document_is_not_lent_to_another():
    evidence = {**EVIDENCE, "slack:B": "Replica lag is the main blocker."}
    scorer = NeedsLeadIn()
    out = check_layer2([{"text": "x", "citations": ["slack:B"]}], evidence, scorer, threshold=0.05, floor=0.01,
                       context={"jira:A": "In Jira, as of 2026-10-10 (10 October 2026):", "slack:B": "In Slack:"})
    assert out.claims == [] and all("In Jira" not in p for p, _ in scorer.pairs)


def test_an_opener_that_points_at_the_claim_before_is_not_scored():
    assert standalone("Another blocker raised in Slack is the slow restore.") == "A blocker raised in Slack is the slow restore."
    assert standalone("Additionally, another issue is replica lag.") == "An issue is replica lag."
    assert standalone("Also, the cutover is blocked.") == "The cutover is blocked."
    assert standalone("The cutover is blocked.") == "The cutover is blocked."


def test_the_claim_shown_keeps_its_own_words():
    scorer = NeedsLeadIn()
    claims = [{"text": "Another blocker is replica lag.", "citations": ["jira:A"]}]
    out = check_layer2(claims, EVIDENCE, scorer, threshold=0.05, floor=0.01, context={"jira:A": lead_in("jira", "2026-10-10")})
    assert out.claims == claims
    assert {claim for _, claim in scorer.pairs} == {"A blocker is replica lag."}

"""The two command-line helpers, against the fake workspace."""
from connectors.slack.build_manifest import build
from connectors.slack.check_rate_limit import measure, verdict
from connectors.slack.fake import FakeSlack, seed_company_a
from fixtures.loader import load

DATA = load()


def test_build_manifest_finds_the_seeded_workspace():
    slack = FakeSlack()
    _, seeded = seed_company_a(slack, DATA)
    manifest, problems = build(slack.client(), DATA)
    assert problems == [] and manifest == seeded


def test_build_manifest_reports_what_is_missing():
    slack = FakeSlack()
    _, seeded = seed_company_a(slack, DATA)
    slack.channels[seeded.channels["C_AUTHPRIV"]]["bot"] = False          # private and the bot was never invited
    slack.channels[seeded.channels["C_PAYINC"]]["bot"] = False            # public, bot not invited
    channel = seeded.channels["C_AUTH"]
    slack.edit(channel, seeded.threads["slack:C_AUTH/thread-1"], "typo in the seeded text")
    manifest, problems = build(slack.client(), DATA)
    assert sorted(manifest.channels) == ["C_AUTH", "C_DBMIG", "C_DBMIGPRIV"]
    assert sorted(manifest.threads) == ["slack:C_DBMIG/thread-1", "slack:C_DBMIG/thread-2", "slack:C_DBMIGPRIV/thread-1"]
    assert len(problems) == 3
    assert any("#auth-private" in p and "not found" in p for p in problems)
    assert any("#payments-incident" in p and "not a member" in p for p in problems)
    assert any("slack:C_AUTH/thread-1" in p and "found 0" in p for p in problems)
    assert not any(doc["body"] in p for p in problems for doc in DATA["documents"]), "message text is never printed"


def test_rate_limit_check_tells_the_three_outcomes_apart():
    slack = FakeSlack()
    _, seeded = seed_company_a(slack, DATA)
    channel = seeded.channels["C_DBMIG"]
    free = measure(slack.client(), channel, 5)
    assert (free["ok"], free["rate_limited"], free["messages_per_call"]) == (5, 0, [2] * 5)
    assert "NOT rate-limited" in verdict(free)
    slack.retry_after = "60"
    slack.throttle = 4
    cut = measure(slack.client(), channel, 4)
    assert (cut["ok"], cut["rate_limited"], cut["retry_after_seconds"]) == (0, 4, [60.0] * 4)
    assert "APPLY" in verdict(cut)
    assert "ordinary tier limit" in verdict({"ok": 8, "rate_limited": 4})

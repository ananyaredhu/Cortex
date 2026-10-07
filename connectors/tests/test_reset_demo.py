"""The simulator part of the demo reset (connectors/reset_demo.py). Slack and Drive are real platforms: their checks
run by hand, `python -m connectors.reset_demo`."""
from connectors.reset_demo import simulators
from simulators.confluence.testing import SeededConfluence
from simulators.jira.testing import SeededJira
from simulators.leakci import LeakCI


def test_planted_documents_are_reported_and_apply_resets_both_simulators():
    confluence, jira = SeededConfluence(), SeededJira()
    clients = {"confluence": confluence.http, "jira": jira.http}
    leakci = LeakCI(confluence.http, jira.http)
    planted = [leakci.plant("confluence", "a"), leakci.plant("jira", "b", mode="level")]
    lines, problems = simulators(False, clients)
    assert lines == ["simulators: answering; 2 Leak-CI documents planted"] and len(problems) == 2
    lines, problems = simulators(True, clients)
    assert problems == [] and leakci.planted() == []
    assert "confluence: reset to the Company A seed" in lines and "jira: reset to the Company A seed" in lines
    assert all(p.doc_id not in {c.doc_id for c in confluence.list_changes(None).changes} for p in planted)
    lines, problems = simulators(False, clients)
    assert lines == ["simulators: answering; 0 Leak-CI documents planted"] and problems == []

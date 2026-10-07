"""The revocation-timing tool, in process: every stage is reached, B's hook is timed, and each revocation is undone."""
from connectors.ingestion import Ingestor, InMemoryStore
from connectors.ingestion.embedding import NullEmbedder
from connectors.ingestion.revocation_timing import measure, scenarios
from simulators.confluence.testing import SeededConfluence
from simulators.jira.testing import SeededJira


def test_every_scenario_reaches_every_stage_and_is_undone():
    sims = {"confluence": SeededConfluence(), "jira": SeededJira()}
    store = InMemoryStore()
    ingestor = Ingestor(sims.values(), store, NullEmbedder())
    ingestor.run_once()
    asked = []

    def enforced(persona: str, doc_id: str) -> bool:     # what B would plug in: does the API refuse now?
        asked.append((persona, doc_id))
        sim = sims[doc_id.split(":", 1)[0]]
        identity = sim.resolve_identity(persona)
        return identity is None or not sim.check_access(identity, doc_id).allowed

    for scenario in scenarios(sims["jira"].http):
        sim = sims[scenario.source]
        stages = measure(scenario, sim, sim.http, store, ingestor, enforced)
        expected = {"check_access", "outbox", "enforced"} | (
            {"resolve_identity"} if scenario.kind == "principal_change" else {"index"})
        assert set(stages) == expected and all(v is not None for v in stages.values()), scenario.name
        identity = sim.resolve_identity(scenario.persona)
        assert sim.check_access(identity, scenario.doc_id).allowed, f"{scenario.name} was not undone"
    assert {p for p, _ in asked} == {"dana@companya.com", "jordan@companya.com", "priya@companya.com"}

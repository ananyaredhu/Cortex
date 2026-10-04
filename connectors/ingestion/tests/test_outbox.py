"""The outbox of permission events (`ingestion_events`): what Workstream B reads to drop caches and audit."""
from connectors.ingestion import FakeEmbedder, IngestionEvent, Ingestor
from connectors.stub.fixture_connector import FixtureConnector
from simulators.confluence.testing import SeededConfluence
from simulators.jira.testing import SeededJira

RUNBOOK = "confluence:PAY/runbook-payment-service"


def _ingestor(store, *connectors) -> Ingestor:
    return Ingestor(connectors, store, FakeEmbedder())   # default sink: the store's outbox


def test_initial_load_and_edits_emit_nothing(store):
    confluence = SeededConfluence()
    ingestor = _ingestor(store, confluence, SeededJira())
    ingestor.run_once()
    confluence.advance("e1")   # a content edit: the ACL is the same
    ingestor.run_once()
    assert store.events_after(0) == []


def test_membership_revocation_lands_in_the_outbox(store):
    slack = FixtureConnector("slack")
    ingestor = _ingestor(store, slack)
    ingestor.run_once()
    slack.advance("e2")
    ingestor.run_once()
    [event] = store.events_after(0)
    assert (event.seq, event.kind, event.source, event.principal, event.token, event.doc_id, event.snapshot_hash) == (
        1, "principal_change", "slack", "user:priya@companya.com", "channel:C_AUTHPRIV", None, None)
    assert event.detected_at and event.observed_at


def test_document_restriction_lands_in_the_outbox_with_the_new_snapshot(store):
    confluence, jira = SeededConfluence(), SeededJira()
    ingestor = _ingestor(store, confluence, jira)
    ingestor.run_once()
    confluence.restrict_document(RUNBOOK, "user:priya@companya.com")
    jira.restrict_document("jira:DBMIG-142", "user:priya@companya.com")
    ingestor.run_once()
    events = store.events_after(0)
    assert [(e.kind, e.source, e.doc_id, e.principal) for e in events] == [
        ("acl_change", "confluence", RUNBOOK, None), ("acl_change", "jira", "jira:DBMIG-142", None)]
    for event in events:
        assert event.snapshot_hash == store.snapshots_of(event.doc_id)[-1].snapshot_hash
        assert event.snapshot_hash == store.chunks_of(event.doc_id)[0].acl_snapshot_hash


def test_a_consumer_reads_past_the_last_seq_it_handled(store):
    for i in range(5):
        seq = store.append_event(IngestionEvent("principal_change", "jira", "2026-10-10T15:00:00Z", "2026-10-10T15:00:01Z",
                                                principal="user:dana@companya.com", token=f"role:P{i}:developer"))
        assert seq == i + 1
    first = store.events_after(0, limit=2)
    assert [e.seq for e in first] == [1, 2]
    rest = store.events_after(first[-1].seq)
    assert [e.seq for e in rest] == [3, 4, 5] and [e.token for e in rest] == [f"role:P{i}:developer" for i in (2, 3, 4)]
    assert store.events_after(5) == []


def test_the_outbox_survives_a_restart(store):
    slack = FixtureConnector("slack")
    _ingestor(store, slack).run_once()
    slack.advance("e2")
    _ingestor(store, slack).run_once()
    _ingestor(store, slack).run_once()   # a new process over the same store: nothing new, nothing lost, no duplicate
    assert [e.seq for e in store.events_after(0)] == [1]

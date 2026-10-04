"""The ingestion pipeline against the simulators and the fixture connector (hand-off 18.2, "Done when")."""
import itertools

from connectors.base import AclEvidence, Change, ChangeBatch, Document, DocumentNotFound
from connectors.ingestion import FakeEmbedder, Ingestor, RecordingSink
from connectors.ingestion.embedding import NullEmbedder
from connectors.ingestion.pipeline import ACL_REWRITTEN, DELETED, INDEXED, PRINCIPAL, UNCHANGED
from connectors.stub.fixture_connector import FixtureConnector
from fixtures.loader import load
from simulators.confluence.testing import SeededConfluence
from simulators.jira.testing import SeededJira

DATA = load()
RUNBOOK = "confluence:PAY/runbook-payment-service"
_SCRIPT_IDS = itertools.count()


def _docs(source: str) -> list[dict]:
    return [d for d in DATA["documents"] if d["source"] == source]


def _ingestor(store, *connectors, **kwargs) -> Ingestor:
    return Ingestor(connectors, store, kwargs.pop("embedder", None) or FakeEmbedder(), **kwargs)


def _text(store, doc_id: str) -> str:
    return "\n\n".join(c.text for c in store.chunks_of(doc_id))


# -- initial load -------------------------------------------------------------------------------
def test_crawl_indexes_every_seeded_document_with_its_fixture_tokens(store):
    connectors = [SeededConfluence(), SeededJira(), FixtureConnector("slack"), FixtureConnector("gdrive")]
    report = _ingestor(store, *connectors).run_once()
    for conn in connectors:
        assert report.count(conn.source, INDEXED) == len(_docs(conn.source))
        for d in _docs(conn.source):
            fetched = conn.fetch(d["doc_id"])
            chunks = store.chunks_of(d["doc_id"])
            assert chunks, d["doc_id"]
            assert [c.chunk_id for c in chunks] == [f"{d['doc_id']}#{i}" for i in range(len(chunks))]
            assert _text(store, d["doc_id"]) == fetched.body.strip()
            for c in chunks:
                assert c.acl_tokens == d["acl"]["tokens"]
                assert c.acl_snapshot_hash == fetched.acl.snapshot_hash
                assert c.source_version == d["version"]
                assert (c.embedding_model, c.embedding_version) == ("fake", "1")
                assert c.embedding is not None and len(c.embedding) == 1024
            [snapshot] = store.snapshots_of(d["doc_id"])
            assert snapshot.tokens == d["acl"]["tokens"] and snapshot.valid_to is None
            assert snapshot.native == fetched.acl.native


def test_crawl_follows_has_more_across_pages(store):
    jira = SeededJira()
    jira._page_size = 2
    report = _ingestor(store, jira).run_once()
    assert report.count("jira", INDEXED) == len(_docs("jira"))
    assert all(store.chunks_of(d["doc_id"]) for d in _docs("jira"))


def test_lag_is_reported_per_source(store):
    report = _ingestor(store, SeededJira()).run_once()
    lag = report.as_json()["freshness_lag_seconds"]["jira"]
    assert lag["count"] == len(_docs("jira")) and 0 <= lag["p50"] <= lag["p95"] <= lag["max"]


# -- restart and idempotence --------------------------------------------------------------------
class _NoCrawl:
    """Wraps a connector and fails the test if a full crawl is started."""

    def __init__(self, inner):
        self._inner = inner
        self.source = inner.source

    def list_changes(self, cursor):
        assert cursor is not None, "a restart must resume from the saved cursor, not crawl again"
        return self._inner.list_changes(cursor)

    def fetch(self, doc_id):
        return self._inner.fetch(doc_id)


def test_restart_resumes_from_the_saved_cursor(store):
    confluence = SeededConfluence()
    _ingestor(store, confluence).run_once()
    confluence.advance("e1")
    embedder = FakeEmbedder()
    report = _ingestor(store, _NoCrawl(confluence), embedder=embedder).run_once()   # a new process, same store
    assert dict(report.actions["confluence"]) == {INDEXED: 1}
    assert embedder.calls == len(store.chunks_of(RUNBOOK))


def test_a_fresh_crawl_removes_what_no_longer_exists(store):
    """A simulator restart loses its change log. Crawling again must also drop documents that are gone."""
    _ingestor(store, SeededJira()).run_once()
    restarted = SeededJira()
    restarted.http.delete("/sim/admin/issues/SEC-17").raise_for_status()
    store.clear_cursor("jira")
    embedder = FakeEmbedder()
    report = _ingestor(store, restarted, embedder=embedder).run_once()
    assert dict(report.actions["jira"]) == {UNCHANGED: len(_docs("jira")) - 1, DELETED: 1}
    assert embedder.calls == 0
    assert store.chunks_of("jira:SEC-17") == [] and store.indexed("jira:SEC-17").deleted is True
    assert store.live_doc_ids("jira") == {d["doc_id"] for d in _docs("jira")} - {"jira:SEC-17"}


def test_a_replayed_crawl_changes_nothing_and_embeds_nothing(store):
    """A crash before the cursor is saved replays the batch: that must be cheap and harmless."""
    jira = SeededJira()
    _ingestor(store, jira).run_once()
    before = {d["doc_id"]: store.chunks_of(d["doc_id"]) for d in _docs("jira")}
    embedder = FakeEmbedder()
    replay = Ingestor([_Scripted("jira", jira, jira.list_changes(None).changes)], store, embedder)
    assert dict(replay.run_once().actions["jira"]) == {UNCHANGED: len(_docs("jira"))}
    assert embedder.calls == 0
    assert {d["doc_id"]: store.chunks_of(d["doc_id"]) for d in _docs("jira")} == before


def test_a_new_embedding_model_reindexes(store):
    jira = SeededJira()
    _ingestor(store, jira).run_once()
    replay = Ingestor([_Scripted("jira", jira, jira.list_changes(None).changes)], store, NullEmbedder())
    assert dict(replay.run_once().actions["jira"]) == {INDEXED: len(_docs("jira"))}
    chunk = store.chunks_of("jira:DBMIG-142")[0]
    assert chunk.embedding is None and chunk.embedding_model == "none"


# -- upsert -------------------------------------------------------------------------------------
def test_edit_replaces_the_chunks_with_the_new_version(store):
    confluence = SeededConfluence()
    ingestor = _ingestor(store, confluence)
    ingestor.run_once()
    assert {c.source_version for c in store.chunks_of(RUNBOOK)} == {"v1"}
    confluence.advance("e1")
    report = ingestor.run_once()
    assert dict(report.actions["confluence"]) == {INDEXED: 1}
    event = next(e for e in DATA["events"] if e["id"] == "e1")
    assert {c.source_version for c in store.chunks_of(RUNBOOK)} == {"v2"}
    assert _text(store, RUNBOOK) == event["patch"]["body"].strip()
    assert len(store.snapshots_of(RUNBOOK)) == 1, "the ACL did not change, so no new snapshot"


# -- acl_change ---------------------------------------------------------------------------------
def test_restriction_rewrites_tokens_without_reembedding(store):
    for conn, doc_id in ((SeededConfluence(), RUNBOOK), (SeededJira(), "jira:DBMIG-142")):
        embedder = FakeEmbedder()
        ingestor = _ingestor(store, conn, embedder=embedder)
        ingestor.run_once()
        before = store.chunks_of(doc_id)
        embedded = embedder.calls
        conn.restrict_document(doc_id, "user:priya@companya.com")
        report = ingestor.run_once()
        assert report.count(conn.source, ACL_REWRITTEN) == 1 and report.count(conn.source, INDEXED) == 0
        assert embedder.calls == embedded
        after = store.chunks_of(doc_id)
        new_hash = conn.fetch(doc_id).acl.snapshot_hash
        assert [(c.chunk_id, c.text, c.embedding) for c in after] == [(c.chunk_id, c.text, c.embedding) for c in before]
        assert all(c.acl_tokens == ["user:priya@companya.com"] and c.acl_snapshot_hash == new_hash for c in after)
        old, new = store.snapshots_of(doc_id)
        assert old.valid_to is not None and old.valid_to == new.valid_from and new.valid_to is None
        assert new.tokens == ["user:priya@companya.com"] and new.snapshot_hash == new_hash != old.snapshot_hash


def test_container_revocation_rewrites_the_childrens_tokens(store):
    confluence = SeededConfluence()
    ingestor = _ingestor(store, confluence)
    ingestor.run_once()
    confluence.revoke_container("confluence:PAY", "public:org")
    ingestor.run_once()
    assert all("public:org" not in c.acl_tokens for c in store.chunks_of(RUNBOOK))
    assert store.chunks_of(RUNBOOK)[0].acl_tokens == confluence.fetch(RUNBOOK).acl.tokens


# -- delete -------------------------------------------------------------------------------------
def test_delete_removes_the_chunks_and_leaves_a_tombstone(store):
    confluence, jira = SeededConfluence(), SeededJira()
    ingestor = _ingestor(store, confluence, jira)
    ingestor.run_once()
    confluence.http.delete("/sim/admin/pages/runbook-payment-service").raise_for_status()
    jira.http.delete("/sim/admin/issues/SEC-17").raise_for_status()
    report = ingestor.run_once()
    for source, doc_id in (("confluence", RUNBOOK), ("jira", "jira:SEC-17")):
        assert report.count(source, DELETED) == 1
        assert store.chunks_of(doc_id) == []
        assert store.indexed(doc_id).deleted is True
        assert store.snapshots_of(doc_id)[-1].valid_to is not None
    assert store.chunks_of("jira:DBMIG-142"), "other documents are untouched"


class _Scripted:
    """A connector whose feed is a fixed list of changes; `fetch` goes to `inner` unless the doc is in `gone`."""

    def __init__(self, source, inner, changes, gone=()):
        self.source = source
        self._inner = inner
        self._changes = list(changes)
        self._gone = set(gone)
        self._end = f"scripted-end-{next(_SCRIPT_IDS)}"   # its own end cursor, so a later script is not mistaken for read

    def list_changes(self, cursor):
        return ChangeBatch([] if cursor == self._end else self._changes, self._end, False)

    def fetch(self, doc_id):
        if doc_id in self._gone:
            raise DocumentNotFound(doc_id)
        return self._inner.fetch(doc_id)


def test_document_that_vanishes_before_fetch_is_treated_as_a_delete(store):
    slack = FixtureConnector("slack")
    _ingestor(store, slack).run_once()
    doc_id = "slack:C_DBMIG/thread-1"
    vanished = _Scripted("slack", slack, [Change("upsert", doc_id, DATA["now"]), Change("acl_change", "slack:never-seen", DATA["now"])],
                         gone={doc_id, "slack:never-seen"})
    report = _ingestor(store, vanished).run_once()
    assert dict(report.actions["slack"]) == {DELETED: 1, UNCHANGED: 1}
    assert store.chunks_of(doc_id) == [] and store.indexed(doc_id).deleted is True
    assert store.indexed("slack:never-seen") is None


def test_a_deleted_document_that_comes_back_is_indexed_again(store):
    slack = FixtureConnector("slack")
    doc_id = "slack:C_AUTH/thread-1"
    _ingestor(store, slack).run_once()
    _ingestor(store, _Scripted("slack", slack, [Change("delete", doc_id, DATA["now"])])).run_once()
    assert store.chunks_of(doc_id) == []
    _ingestor(store, _Scripted("slack", slack, [Change("upsert", doc_id, DATA["now"])])).run_once()
    assert store.chunks_of(doc_id) and store.indexed(doc_id).deleted is False
    assert [s.valid_to is None for s in store.snapshots_of(doc_id)] == [False, True]


def test_document_with_no_body_is_indexed_by_its_title(store):
    doc = Document("gdrive:empty", "gdrive", "file", "Quarterly plan", "https://example.invalid/empty", "", None, [], None,
                   "2026-10-01T00:00:00Z", "2026-10-01T00:00:00Z", "1",
                   AclEvidence(["public:org"], {"shared": "org"}, "sha256:abc", "2026-10-01T00:00:00Z"))

    class _One:
        source = "gdrive"

        def fetch(self, doc_id):
            return doc

    _ingestor(store, _Scripted("gdrive", _One(), [Change("upsert", "gdrive:empty", DATA["now"])])).run_once()
    assert [c.text for c in store.chunks_of("gdrive:empty")] == ["Quarterly plan"]


# -- principal_change ---------------------------------------------------------------------------
def test_principal_change_reaches_the_sink_and_leaves_the_index_alone(store):
    slack = FixtureConnector("slack")
    sink, embedder = RecordingSink(), FakeEmbedder()
    ingestor = _ingestor(store, slack, embedder=embedder, sink=sink)
    ingestor.run_once()
    before = {d["doc_id"]: store.chunks_of(d["doc_id"]) for d in _docs("slack")}
    embedded = embedder.calls
    slack.advance("e2")
    report = ingestor.run_once()
    assert dict(report.actions["slack"]) == {PRINCIPAL: 1}
    [event] = sink.events
    assert (event.kind, event.source, event.principal, event.token, event.doc_id) == (
        "principal_change", "slack", "user:priya@companya.com", "channel:C_AUTHPRIV", None)
    assert embedder.calls == embedded
    assert {d["doc_id"]: store.chunks_of(d["doc_id"]) for d in _docs("slack")} == before
    assert all(len(store.snapshots_of(d["doc_id"])) == 1 for d in _docs("slack"))


def test_a_failing_sink_leaves_the_cursor_so_the_event_is_retried(store):
    class _Down:
        def emit(self, event):
            raise ConnectionError("audit service is down")

    slack = FixtureConnector("slack")
    _ingestor(store, slack).run_once()
    cursor = store.get_cursor("slack")
    slack.advance("e2")
    try:
        _ingestor(store, slack, sink=_Down()).run_once()
        raise AssertionError("the failure must surface")
    except ConnectionError:
        pass
    assert store.get_cursor("slack") == cursor
    sink = RecordingSink()
    _ingestor(store, slack, sink=sink).run_once()
    assert len(sink.events) == 1

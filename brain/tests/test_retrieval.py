"""Hybrid retrieval over both index readers. Postgres runs when the database is up."""
import pytest

from brain.retrieval import MemoryIndex, PostgresIndex, hybrid_search, query_terms
from connectors.ingestion import FakeEmbedder, Ingestor, InMemoryStore
from connectors.ingestion.pg_store import PostgresStore
from connectors.stub.fixture_connector import FixtureConnector
from fixtures.loader import load

DATA = load()
TOKENS = {p["id"]: p["tokens"] for p in DATA["personas"]}
SOURCES = ("confluence", "jira", "slack", "gdrive")
Q1 = "What's the status of the database migration, and were there blockers raised in Slack last week?"


@pytest.fixture(params=["memory", "postgres"])
def index(request):
    connectors = [FixtureConnector(s) for s in SOURCES]
    if request.param == "memory":
        store = InMemoryStore()
        Ingestor(connectors, store, FakeEmbedder()).run_once()
        return MemoryIndex(store)
    conn = request.getfixturevalue("pg_conn")
    Ingestor(connectors, PostgresStore(conn), FakeEmbedder()).run_once()
    return PostgresIndex(conn)


def test_query_terms_drop_stopwords_and_keep_order():
    assert query_terms(Q1)[:4] == ["status", "database", "migration", "blockers"]


def test_keyword_leg_finds_the_migration_documents_priya_may_see(index):
    found = {c.doc_id for c in hybrid_search(index, Q1, TOKENS["priya"], SOURCES)}
    assert {"jira:DBMIG-142", "slack:C_DBMIG/thread-1"} <= found
    assert "slack:C_DBMIGPRIV/thread-1" not in found          # private channel, not a member


def test_prefilter_hides_what_the_asker_holds_no_token_for(index):
    sam = hybrid_search(index, "security incident report Q3 breach", TOKENS["sam"], SOURCES)
    assert all(c.doc_id not in ("confluence:SEC/q3-breach-report", "jira:SEC-17") for c in sam)
    dana = hybrid_search(index, "security incident report Q3 breach", TOKENS["dana"], SOURCES)
    assert "confluence:SEC/q3-breach-report" in {c.doc_id for c in dana}


def test_no_tokens_means_no_candidates(index):
    assert hybrid_search(index, Q1, [], SOURCES) == []


def test_source_fan_out_respects_the_requested_sources(index):
    only_jira = hybrid_search(index, Q1, TOKENS["priya"], ["jira"])
    assert only_jira and all(c.source == "jira" for c in only_jira)


def test_vector_leg_is_ignored_when_its_distance_is_too_large(index):
    """Fake vectors are random, so the vector leg must contribute nothing: results equal the keyword leg's."""
    q = FakeEmbedder().embed([Q1])[0]
    with_vec = hybrid_search(index, Q1, TOKENS["priya"], SOURCES, vector=q, model="fake")
    without = hybrid_search(index, Q1, TOKENS["priya"], SOURCES)
    assert [c.doc_id for c in with_vec] == [c.doc_id for c in without]
    close = hybrid_search(index, Q1, TOKENS["priya"], SOURCES, vector=q, model="fake", max_distance=2.0)
    assert any(c.vector_rank is not None for c in close)


def test_document_rows_and_visible_listing(index):
    row = index.document("jira:DBMIG-142")
    assert row and row.source == "jira" and row.title.startswith("DBMIG-142") and row.parent_id == "jira:DBMIG"
    assert index.document("jira:NOPE") is None
    visible = {d.doc_id for d in index.documents_visible(TOKENS["sam"])}
    assert visible == {d["doc_id"] for d in DATA["documents"] if set(d["acl"]["tokens"]) & set(TOKENS["sam"])}

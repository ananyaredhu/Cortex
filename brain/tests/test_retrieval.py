"""Hybrid retrieval over both index readers. Postgres runs when the database is up."""
import threading

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


def test_hidden_listing_is_the_complement_of_visible(index):
    """`documents_hidden` (fix F5) is every live document the asker's tokens do not reach: the leak scan's denied set."""
    for who in ("sam", "priya", "dana"):
        hidden = {d.doc_id for d in index.documents_hidden(TOKENS[who])}
        visible = {d.doc_id for d in index.documents_visible(TOKENS[who])}
        assert hidden.isdisjoint(visible) and hidden | visible == {d["doc_id"] for d in DATA["documents"]}
    assert {"confluence:SEC/q3-breach-report", "jira:SEC-17"} <= {d.doc_id for d in index.documents_hidden(TOKENS["sam"])}
    assert len(index.documents_hidden([])) == len(DATA["documents"])               # no tokens: everything is hidden


def test_container_listings_for_the_project_registry(index):
    """`parents_visible`, `documents_under` and `documents_all` agree with `documents_visible` on both stores."""
    for who in ("sam", "priya", "maya", "dana"):
        visible = index.documents_visible(TOKENS[who])
        assert index.parents_visible(TOKENS[who]) == {d.parent_id for d in visible if d.parent_id}
    assert "slack:C_DBMIGPRIV" in index.parents_visible(TOKENS["maya"])
    assert "slack:C_DBMIGPRIV" not in index.parents_visible(TOKENS["priya"])
    assert index.parents_visible([]) == set()
    under = index.documents_under(["jira:DBMIG", "slack:C_DBMIGPRIV"], TOKENS["priya"], 10)
    assert {d.parent_id for d in under} == {"jira:DBMIG"} and len(under) == 7                 # the private channel is not hers
    assert len(index.documents_under(["jira:DBMIG"], TOKENS["priya"], 1)) == 1
    assert index.documents_under(["jira:DBMIG"], [], 10) == []
    assert {d.doc_id for d in index.documents_all()} == {d["doc_id"] for d in DATA["documents"]}


def test_concurrent_searches_share_one_connection_safely(index):
    """Request threads share one index connection (the split-screen asks as three people at once). Two vector
    legs entering a transaction block together was OutOfOrderTransactionNesting on Postgres."""
    q = FakeEmbedder().embed([Q1])[0]
    errors: list[Exception] = []

    def search() -> None:
        try:
            hybrid_search(index, Q1, TOKENS["priya"], SOURCES, vector=q, model="fake", max_distance=2.0)
        except Exception as exc:  # noqa: BLE001 - the test reports whatever a thread raised
            errors.append(exc)

    threads = [threading.Thread(target=search) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []


# -- the title leg: a title is not in the chunks, but it carries ticket keys and names --------------------------------------
def test_a_ticket_key_finds_its_ticket_by_title(index):
    found = [c.doc_id for c in hybrid_search(index, "what is DBMIG-150 about", TOKENS["priya"], SOURCES)]
    assert "jira:DBMIG-150" in found[:2]                          # DBMIG-142's body also names it, so the two may share the top
    assert [c.doc_id for c in hybrid_search(index, "SEC-17", TOKENS["dana"], SOURCES)][:1] == ["jira:SEC-17"]


def test_the_title_leg_can_be_switched_off(index):
    index.title_leg = False
    try:
        found = [c.doc_id for c in hybrid_search(index, "what is DBMIG-150 about", TOKENS["priya"], SOURCES)]
    finally:
        index.title_leg = True
    assert "jira:DBMIG-150" not in found[:1]                                   # the body alone does not say its own key


def test_a_title_hit_still_needs_the_asker_to_hold_a_token(index):
    assert [c.doc_id for c in hybrid_search(index, "SEC-17", TOKENS["sam"], SOURCES)] == []
    assert hybrid_search(index, "SEC-17", [], SOURCES) == []
    assert "jira:SEC-17" not in {c.doc_id for c in hybrid_search(index, "SEC-17 token replay", TOKENS["priya"], SOURCES)}


def test_title_words_alone_find_a_document_whose_body_lacks_them(index):
    found = [c.doc_id for c in hybrid_search(index, "payment service incident runbook", TOKENS["priya"], SOURCES)]
    assert found[0] == "confluence:PAY/runbook-payment-service"


def test_a_plain_word_matches_a_title_word_and_a_partial_key_does_not(index):
    assert [h.doc_id for h in index.title_search("jira", ["cutover", "blocked"], TOKENS["priya"], 5)][:1] == ["jira:DBMIG-142"]
    assert index.title_search("jira", ["dbmig-999"], TOKENS["priya"], 5) == []
    assert index.title_search("jira", ["dbmig-150"], TOKENS["priya"], 5)[0].score == 2.0         # a whole key counts double

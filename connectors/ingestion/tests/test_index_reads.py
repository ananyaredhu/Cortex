"""The reads Workstream B's retrieval makes on the index (connector-interface.md, "Reading the index").

Postgres only: the point is that db/init.sql, filled by ingestion, supports these queries as written in the contract.
"""
import pytest

from connectors.ingestion import FakeEmbedder, Ingestor
from connectors.ingestion.pg_store import PostgresStore
from connectors.stub.fixture_connector import FixtureConnector
from fixtures.loader import load

DATA = load()
SOURCES = ("slack", "gdrive", "confluence", "jira")
PERSONAS = {p["id"]: p["tokens"] for p in DATA["personas"]}
BREACH = "confluence:SEC/q3-breach-report"

# The reference vector query from the contract: approximate search that keeps scanning the index until enough rows
# pass the ACL prefilter, then re-sorted because relaxed_order may return rows slightly out of order.
VECTOR_QUERY = """
WITH relaxed AS MATERIALIZED (
    SELECT chunk_id, doc_id, embedding <=> %(q)s::vector AS distance
    FROM chunks
    WHERE acl_tokens && %(tokens)s AND embedding_model = %(model)s AND embedding IS NOT NULL
    ORDER BY distance
    LIMIT %(k)s
)
SELECT chunk_id, doc_id, distance FROM relaxed ORDER BY distance
"""
KEYWORD_QUERY = """
SELECT chunk_id, doc_id, ts_rank_cd(tsv, query) AS rank
FROM chunks, websearch_to_tsquery('english', %(text)s) AS query
WHERE tsv @@ query AND acl_tokens && %(tokens)s
ORDER BY rank DESC
LIMIT %(k)s
"""


def _visible(tokens: list[str]) -> set[str]:
    return {d["doc_id"] for d in DATA["documents"] if set(d["acl"]["tokens"]) & set(tokens)}


@pytest.fixture
def index(store):
    if not isinstance(store, PostgresStore):
        pytest.skip("reads are a Postgres concern")
    Ingestor([FixtureConnector(s) for s in SOURCES], store, FakeEmbedder()).run_once()
    return store._conn


def _vector_of(conn, doc_id: str) -> str:
    return conn.execute("SELECT embedding::text FROM chunks WHERE doc_id = %s ORDER BY position LIMIT 1",
                        (doc_id,)).fetchone()[0]


def test_prefilter_finds_exactly_the_documents_each_persona_holds_a_token_for(index):
    for persona, tokens in PERSONAS.items():
        rows = index.execute("SELECT DISTINCT doc_id FROM chunks WHERE acl_tokens && %s", (tokens,)).fetchall()
        assert {r[0] for r in rows} == _visible(tokens), persona
    assert index.execute("SELECT count(*) FROM chunks WHERE acl_tokens && %s", ([],)).fetchone()[0] == 0


def test_every_chunk_carries_its_documents_source(index):
    rows = index.execute("SELECT c.source, d.source FROM chunks c JOIN documents d USING (doc_id)").fetchall()
    assert rows and all(chunk == doc for chunk, doc in rows)
    assert {r[0] for r in rows} == set(SOURCES)


def test_vector_search_through_the_index_fills_k_with_allowed_chunks_only(index):
    tokens = PERSONAS["jordan"]                       # sees the fewest documents
    allowed = index.execute("SELECT count(*) FROM chunks WHERE acl_tokens && %s", (tokens,)).fetchone()[0]
    k = 5
    assert 0 < allowed
    params = {"q": _vector_of(index, BREACH), "tokens": tokens, "model": "fake", "k": k}   # jordan cannot see BREACH
    with index.transaction():
        # Force the vector index with a narrow search beam: without iterative scanning the filter would starve the
        # result. (On a narrow ACL Postgres may rightly prefer the GIN prefilter plus an exact sort; both are correct.)
        index.execute("SET LOCAL enable_seqscan = off")
        index.execute("SET LOCAL enable_bitmapscan = off")
        index.execute("SET LOCAL hnsw.ef_search = 2")
        index.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
        plan = "\n".join(r[0] for r in index.execute("EXPLAIN " + VECTOR_QUERY, params).fetchall())
        rows = index.execute(VECTOR_QUERY, params).fetchall()
    assert "chunks_embedding_idx" in plan
    assert len(rows) == min(k, allowed)
    assert {r[1] for r in rows} <= _visible(tokens)
    assert [r[2] for r in rows] == sorted(r[2] for r in rows)


def test_keyword_search_respects_the_prefilter(index):
    found = index.execute(KEYWORD_QUERY, {"text": "breach", "tokens": PERSONAS["priya"], "k": 10}).fetchall()
    assert BREACH not in {r[1] for r in found}
    security = ["group:confluence:security-team"]
    found = index.execute(KEYWORD_QUERY, {"text": "breach", "tokens": security, "k": 10}).fetchall()
    assert BREACH in {r[1] for r in found}

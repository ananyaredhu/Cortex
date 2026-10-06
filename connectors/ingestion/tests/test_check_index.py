"""check_index on Postgres: a faithful index passes, and each kind of drift is reported."""
import pytest

from connectors.gdrive.manifest import SeedManifest as DriveManifest
from connectors.ingestion import FakeEmbedder, Ingestor
from connectors.ingestion.check_index import Ids, check
from connectors.ingestion.pg_store import PostgresStore
from connectors.slack.manifest import SeedManifest as SlackManifest
from connectors.stub.fixture_connector import FixtureConnector
from fixtures.loader import load

DATA = load()
SOURCES = ["confluence", "jira", "slack", "gdrive"]
BREACH = "confluence:SEC/q3-breach-report"
FIXTURE_IDS = Ids(SlackManifest(), DriveManifest())


@pytest.fixture
def conn(store):
    if not isinstance(store, PostgresStore):
        pytest.skip("check_index reads Postgres")
    Ingestor([FixtureConnector(s) for s in SOURCES], store, FakeEmbedder()).run_once()
    return store._conn


def _problems(conn, **kwargs) -> list[str]:
    return check(conn, DATA, FIXTURE_IDS, sources=SOURCES, model=kwargs.get("model", "fake"))[1]


def test_a_faithful_index_passes(conn):
    lines, problems = check(conn, DATA, FIXTURE_IDS, sources=SOURCES, model="fake")
    assert problems == []
    assert lines[0].startswith("indexed: confluence 4 documents") and "not in the fixtures" not in lines[0]
    assert "priya: prefilter finds 15 of the 18 seeded documents" in lines


def test_widened_acl_is_reported_per_document_and_per_persona(conn):
    conn.execute("UPDATE chunks SET acl_tokens = %s WHERE doc_id = %s", (["public:org"], BREACH))
    problems = _problems(conn)
    assert any(p.startswith(f"{BREACH}: readable by") for p in problems)
    assert not any(p.startswith(f"{BREACH}: chunks do not carry") for p in problems)   # hash untouched
    assert any(p.startswith("priya: prefilter finds ['confluence:SEC/q3-breach-report'] too many") for p in problems)


def test_missing_document_stale_snapshot_and_wrong_model_are_reported(conn):
    conn.execute("DELETE FROM chunks WHERE doc_id = 'jira:SEC-17'")
    conn.execute("UPDATE chunks SET acl_snapshot_hash = 'sha256:old' WHERE doc_id = %s", (BREACH,))
    problems = _problems(conn)
    assert "jira:SEC-17: not in the index" in problems
    assert f"{BREACH}: chunks do not carry the current ACL snapshot" in problems
    assert any(p.endswith("chunks without a bge-m3 vector") for p in _problems(conn, model="bge-m3"))


def test_slack_tokens_and_ids_go_through_the_manifest():
    manifest = SlackManifest(channels={"C_DBMIG": "C0REAL1"}, threads={"slack:C_DBMIG/thread-1": "1700000000.000100"})
    ids = Ids(manifest, DriveManifest(files={"gdrive:postmortem-pay-outage": "1RealPm"}))
    assert ids.real_doc("slack:C_DBMIG/thread-1") == "slack:C0REAL1/1700000000.000100"
    assert ids.real_doc("gdrive:postmortem-pay-outage") == "gdrive:1RealPm"
    assert ids.real_doc("jira:SEC-17") == "jira:SEC-17"
    assert ids.real_tokens(["channel:C_DBMIG", "public:org"]) == ["channel:C0REAL1", "public:org"]
    assert ids.fixture_tokens(["channel:C0REAL1", "user:priya@companya.com"]) == ["channel:C_DBMIG", "user:priya@companya.com"]

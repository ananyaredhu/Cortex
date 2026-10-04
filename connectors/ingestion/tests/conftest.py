"""Every pipeline test runs twice: on the in-memory store, and on Postgres when the local database is up.

The Postgres run uses a throwaway schema loaded from db/init.sql, so it never touches the data in `public`.
Without a reachable database (CI, a teammate without Docker) those runs are skipped.
"""
import os
import uuid
from pathlib import Path

import psycopg
import pytest

from connectors.ingestion.pg_store import DEFAULT_URL, PostgresStore
from connectors.ingestion.store import InMemoryStore

_INIT_SQL = Path(__file__).resolve().parents[3] / "db" / "init.sql"


def _connect() -> psycopg.Connection:
    return psycopg.connect(os.environ.get("DATABASE_URL") or DEFAULT_URL, autocommit=True, connect_timeout=2)


@pytest.fixture(scope="session")
def database_up() -> bool:
    """Probe once per run: a connection attempt to a stopped database takes seconds."""
    try:
        _connect().close()
    except psycopg.OperationalError:
        return False
    return True


@pytest.fixture(params=["memory", "postgres"])
def store(request):
    if request.param == "memory":
        yield InMemoryStore()
        return
    if not request.getfixturevalue("database_up"):
        pytest.skip("Postgres is not reachable (start it with `docker compose up -d`)")
    conn = _connect()
    schema = f"ingest_test_{uuid.uuid4().hex[:12]}"
    conn.execute(f"CREATE SCHEMA {schema}")
    try:
        conn.execute(f"SET search_path TO {schema}, public")
        conn.execute(_INIT_SQL.read_text(encoding="utf-8"))
        yield PostgresStore(conn)
    finally:
        conn.execute(f"DROP SCHEMA {schema} CASCADE")
        conn.close()

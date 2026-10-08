"""Shared fixtures. Postgres tests use a throwaway schema from db/init.sql and are skipped when the database is down,
the same way connectors/ingestion/tests do."""
import os
import uuid
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from brain.api.app import create_app
from brain.runtime import fixture_runtime
from connectors.ingestion.pg_store import DEFAULT_URL

_INIT_SQL = Path(__file__).resolve().parents[2] / "db" / "init.sql"


def _connect() -> psycopg.Connection:
    return psycopg.connect(os.environ.get("DATABASE_URL") or DEFAULT_URL, autocommit=True, connect_timeout=2)


@pytest.fixture(scope="session")
def database_up() -> bool:
    try:
        _connect().close()
    except psycopg.OperationalError:
        return False
    return True


@pytest.fixture
def pg_conn(database_up):
    if not database_up:
        pytest.skip("Postgres is not reachable (start it with `docker compose up -d`)")
    conn = _connect()
    schema = f"brain_test_{uuid.uuid4().hex[:12]}"
    conn.execute(f"CREATE SCHEMA {schema}")
    try:
        conn.execute(f"SET search_path TO {schema}, public")
        conn.execute(_INIT_SQL.read_text(encoding="utf-8"))
        yield conn
    finally:
        conn.execute(f"DROP SCHEMA {schema} CASCADE")
        conn.close()


@pytest.fixture
def runtime():
    return fixture_runtime()


@pytest.fixture
def client(runtime):
    return TestClient(create_app(runtime))


def headers(persona: str) -> dict:
    return {"Authorization": f"Bearer dev:{persona}"}

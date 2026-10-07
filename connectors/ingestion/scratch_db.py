"""Throwaway databases for measurement runs (scale_run, revocation_timing), beside the demo index and never it."""
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import psycopg

_INIT_SQL = Path(__file__).resolve().parents[2] / "db" / "init.sql"


def database_name(url: str) -> str:
    return urlparse(url).path.lstrip("/")


def prepare(url: str, *, prefix: str, reset: bool = False) -> None:
    """Create the database `url` names from db/init.sql if it is missing (or always, with reset). Refuses any
    database whose name does not start with `prefix`, so the demo index can never be dropped by mistake."""
    name = database_name(url)
    if not name.startswith(prefix):
        raise SystemExit(f"refusing database {name!r}: this tool only writes to a database named {prefix}...")
    admin = urlunparse(urlparse(url)._replace(path="/postgres"))
    with psycopg.connect(admin, autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()
        if exists and reset:
            conn.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
            exists = None
        if not exists:
            conn.execute(f'CREATE DATABASE "{name}"')
    with psycopg.connect(url, autocommit=True) as conn:
        if conn.execute("SELECT to_regclass('chunks')").fetchone()[0] is None:
            conn.execute(_INIT_SQL.read_text(encoding="utf-8"))

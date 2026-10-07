"""Ingest the scale seed (simulators/scale.py) into its own database, time it, and check the index at that scale.

    SIM_SEED=scale uvicorn simulators.confluence.app:app --port 8101
    SIM_SEED=scale uvicorn simulators.slack.app:app --port 8103
    python -m connectors.ingestion.scale_run --embedder none           # throughput without embedding
    python -m connectors.ingestion.scale_run --embedder fake --reset   # real vectors' shape, for the index checks
    python -m connectors.ingestion.scale_run --check-only              # checks on what is already there

It writes only to SCALE_DATABASE_URL (default: the `brain_scale` database on the local server), creating it from
db/init.sql if needed, and refuses the demo database. `--reset` drops and recreates that database.

Checks, from the generator's spec and not from the simulators' code:
1. every generated page and thread is indexed, with the tokens the spec gives it;
2. each persona's ACL prefilter (`acl_tokens && tokens`) finds exactly the generated documents the spec lets them read;
3. the live `check_access` agrees with the spec on a sample, allowed and denied;
4. the vector query (with the iterative scan, contract rule 3) and the keyword query return k rows per persona,
   all inside the prefilter, and how long they take.
Prints counts, persona names and timings only.
"""
import argparse
import json
import os
import random
import re
import time
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import psycopg

from connectors.env import load_dotenv
from connectors.ingestion.embedding import FakeEmbedder, from_env
from connectors.ingestion.freshness import percentile
from connectors.ingestion.pg_store import DEFAULT_URL, PostgresStore
from connectors.ingestion.pipeline import Ingestor
from fixtures.loader import load
from simulators.confluence import ConfluenceConnector
from simulators.scale import PUBLIC_ORG, Company, Scale, generate
from simulators.slack.connector import connector as slack_sim

_INIT_SQL = Path(__file__).resolve().parents[2] / "db" / "init.sql"
DEFAULT_SCALE_URL = "postgresql://brain:brain@localhost:5432/brain_scale"
K = 10


# -- the database ---------------------------------------------------------------------------------
def _database(url: str) -> str:
    return urlparse(url).path.lstrip("/")


def prepare(url: str, *, reset: bool) -> None:
    """Create the scale database from db/init.sql if it is missing (or always, with reset). Never the demo one."""
    name = _database(url)
    if not name.startswith("brain_scale"):
        raise SystemExit(f"refusing database {name!r}: the scale run only writes to a database named brain_scale...")
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


def _embedder(name: str):
    return FakeEmbedder() if name == "fake" else from_env(name)


# -- ingestion ------------------------------------------------------------------------------------
def ingest(store: PostgresStore, connectors: list, embedder) -> dict:
    ingestor = Ingestor(connectors, store, embedder)
    out = {}
    for conn in connectors:
        started = time.monotonic()
        actions = ingestor.run_source(conn)
        seconds = time.monotonic() - started
        docs, chunks = store._conn.execute(
            "SELECT count(DISTINCT doc_id), count(*) FROM chunks WHERE source = %s", (conn.source,)).fetchone()
        out[conn.source] = {"seconds": round(seconds, 1), "actions": dict(actions), "documents": docs, "chunks": chunks,
                            "documents_per_second": round(sum(actions.values()) / seconds, 1) if seconds else None}
    return out


# -- checks ---------------------------------------------------------------------------------------
_GENERATED = re.compile(r"^(confluence:S\d{3}/|slack:CS\d{5}/)")   # the generator's spaces and channels, not Company A's


def _generated(doc_id: str) -> bool:
    return bool(_GENERATED.match(doc_id))


def _expected_tokens(company: Company) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    return company.readers(), company.channel_tokens()


def _tokens_of_doc(doc_id: str, pages: dict, channels: dict) -> list[str] | None:
    if doc_id.startswith("confluence:"):
        return pages.get(doc_id)
    return channels.get(doc_id.split(":", 1)[1].split("/", 1)[0])


def check(store: PostgresStore, company: Company, connectors: dict, embedder, *, sample: int = 20, seed: int = 1) -> dict:
    conn = store._conn
    pages, channels = _expected_tokens(company)
    rows = conn.execute("SELECT DISTINCT ON (doc_id) doc_id, acl_tokens FROM chunks ORDER BY doc_id").fetchall()
    indexed = {d: sorted(t) for d, t in rows if _generated(d)}
    wrong_tokens = [d for d, t in indexed.items() if t != sorted(_tokens_of_doc(d, pages, channels) or [])]
    slack_expected = len(company.threads)
    result: dict = {
        "indexed": {"confluence_pages": sum(d.startswith("confluence:") for d in indexed), "confluence_expected": len(pages),
                    "slack_threads": sum(d.startswith("slack:") for d in indexed), "slack_expected": slack_expected},
        "documents_with_wrong_tokens": len(wrong_tokens),
        "personas": {},
    }
    rng = random.Random(seed)
    fixtures = load()
    model = embedder.model
    for persona in fixtures["personas"]:
        email = persona["email"]
        extra = company.tokens_of(email)
        if PUBLIC_ORG not in persona["tokens"]:
            extra.discard(PUBLIC_ORG)          # not in the org (Sam): the generated org-wide grants do not reach them
        tokens = sorted(set(persona["tokens"]) | extra)
        expected = {d for d, t in indexed.items() if set(t) & set(tokens)}
        found = {r[0] for r in conn.execute("SELECT DISTINCT doc_id FROM chunks WHERE acl_tokens && %s", (tokens,)).fetchall()}
        found = {d for d in found if _generated(d)}
        report = {"may_read": len(expected), "prefilter_finds": len(found),
                  "prefilter_extra": len(found - expected), "prefilter_missing": len(expected - found)}

        # Live check_access on a sample, through the connectors, against the spec.
        disagree = 0
        for source, live in connectors.items():
            identity = live.resolve_identity(email)
            mine = sorted(d for d in indexed if d.startswith(source + ":"))
            allowed = [d for d in mine if d in expected]
            denied = [d for d in mine if d not in expected]
            picks = rng.sample(allowed, min(sample, len(allowed))) + rng.sample(denied, min(sample, len(denied)))
            for doc_id in picks:
                ok = identity is not None and live.check_access(identity, doc_id).allowed
                disagree += ok != (doc_id in expected)
        report["live_check_disagreements"] = disagree

        # Query latency, with the contract's reference queries.
        vector_ms, keyword_ms, vector_rows, keyword_rows, outside = [], [], [], [], 0
        for _ in range(10):
            question = " ".join(rng.sample(("migration", "runbook", "incident", "payment", "latency", "rollback",
                                            "access", "token", "vendor", "release", "schema", "dashboard"), 2))
            query = embedder.embed([question])[0] if model != "none" else None
            if query is not None:
                started = time.perf_counter()
                with conn.transaction():
                    conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
                    got = conn.execute(
                        "WITH relaxed AS MATERIALIZED (SELECT doc_id, embedding <=> %s::vector AS distance FROM chunks"
                        " WHERE acl_tokens && %s AND embedding_model = %s AND embedding IS NOT NULL"
                        " ORDER BY distance LIMIT %s) SELECT doc_id FROM relaxed ORDER BY distance",
                        ("[" + ",".join(map(repr, query)) + "]", tokens, model, K)).fetchall()
                vector_ms.append((time.perf_counter() - started) * 1000)
                vector_rows.append(len(got))
                outside += sum(1 for (d,) in got if _generated(d) and d not in expected)
            started = time.perf_counter()
            got = conn.execute(
                "SELECT doc_id FROM chunks, websearch_to_tsquery('english', %s) AS query"
                " WHERE tsv @@ query AND acl_tokens && %s ORDER BY ts_rank_cd(tsv, query) DESC LIMIT %s",
                (question, tokens, K)).fetchall()
            keyword_ms.append((time.perf_counter() - started) * 1000)
            keyword_rows.append(len(got))
            outside += sum(1 for (d,) in got if _generated(d) and d not in expected)
        if vector_ms:
            report["vector_ms"] = {"p50": round(percentile(sorted(vector_ms), 50), 1), "p95": round(percentile(sorted(vector_ms), 95), 1)}
            report["vector_rows_min"] = min(vector_rows)
        report["keyword_ms"] = {"p50": round(percentile(sorted(keyword_ms), 50), 1), "p95": round(percentile(sorted(keyword_ms), 95), 1)}
        report["keyword_rows_min"] = min(keyword_rows)
        report["results_outside_prefilter"] = outside
        result["personas"][persona["id"]] = report
    return result


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m connectors.ingestion.scale_run", description=__doc__.splitlines()[0])
    parser.add_argument("--embedder", default="none", choices=("none", "fake", "bge-m3"),
                        help="none: throughput only; fake: deterministic local vectors, for the index checks; bge-m3: the real model")
    parser.add_argument("--reset", action="store_true", help="drop and recreate the scale database first")
    parser.add_argument("--check-only", action="store_true", help="skip ingestion, run the checks on what is indexed")
    args = parser.parse_args()
    load_dotenv()
    url = os.environ.get("SCALE_DATABASE_URL") or DEFAULT_SCALE_URL
    if url == (os.environ.get("DATABASE_URL") or DEFAULT_URL):
        raise SystemExit("SCALE_DATABASE_URL must not be the demo database")
    prepare(url, reset=args.reset)
    company = generate(Scale.from_env())
    connectors = {"confluence": ConfluenceConnector.from_url(os.environ.get("CONFLUENCE_SIM_URL") or "http://localhost:8101",
                                                             timeout=10.0),
                  "slack": slack_sim(os.environ.get("SLACK_SIM_URL") or "http://localhost:8103")}
    embedder = _embedder(args.embedder)
    store = PostgresStore.connect(url)
    try:
        out = {"scale": vars(company.scale), "embedder": embedder.model}
        if not args.check_only:
            out["ingestion"] = ingest(store, list(connectors.values()), embedder)
        out["checks"] = check(store, company, connectors, embedder)
        print(json.dumps(out, indent=2))
    finally:
        store.close()


if __name__ == "__main__":
    main()

"""Revocation-to-enforcement timing, B's stage: `python -m brain.timing [--rounds 5]`.

Runs Workstream A's scenarios (`connectors/ingestion/revocation_timing.py`) with the `enforced` hook filled in: after
each revocation, the Brain is asked, as the affected persona, a question that cited the document before, until the
answer no longer cites it. The Brain here reads the timing database that A's script fills (keyword leg only: that
index has no vectors), talks to the same simulators, and syncs the outbox before every request, as the API does.
Prints persona names, fixture document IDs and timings only.
"""
import argparse
import json
import os

import httpx

from brain.audit.store import MemoryAuditStore
from brain.auth import Principal
from brain.config import Settings
from brain.gateway import TemplateGenerator
from brain.pipeline.graph import AskRequest, Brain
from brain.policy.events import OutboxConsumer
from brain.retrieval import PostgresIndex
from connectors.env import load_dotenv
from connectors.ingestion.pg_store import DEFAULT_URL, PostgresStore
from connectors.ingestion.revocation_timing import run
from fixtures.loader import load
from simulators.confluence import ConfluenceConnector
from simulators.jira import JiraConnector

QUESTIONS = {   # doc_id -> a question whose answer cites it while the persona may read it
    "confluence:SEC/q3-breach-report": "Show me the security incident report from the Q3 breach",
    "confluence:ENG/auth-service-decision": "What was decided about the auth service token format?",
    "jira:DBMIG-142": "Why is the database migration cutover blocked by replica lag?",
    "jira:PAYINC-10": "Which ticket adds the circuit breaker to the payment client?",
}


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m brain.timing", description=__doc__.splitlines()[0])
    parser.add_argument("--rounds", type=int, default=5)
    args = parser.parse_args()
    load_dotenv()
    url = os.environ.get("TIMING_DATABASE_URL") or DEFAULT_URL.rsplit("/", 1)[0] + "/brain_timing"
    os.environ.setdefault("TIMING_DATABASE_URL", url)
    personas = {p["email"]: p for p in load()["personas"]}
    settings = Settings(floor_latency_ms=0, sources=("confluence", "jira"), decision_ttl_s=15.0)
    confluence_url = os.environ.get("CONFLUENCE_SIM_URL") or "http://127.0.0.1:8101"
    jira_url = os.environ.get("JIRA_SIM_URL") or "http://127.0.0.1:8102"
    sims = {"confluence": ConfluenceConnector(httpx.Client(base_url=confluence_url, timeout=10)),
            "jira": JiraConnector(httpx.Client(base_url=jira_url, timeout=10))}
    state = {"brain": None, "consumer": None}

    def brain() -> Brain:
        if state["brain"] is None:       # the timing database exists only once A's `run` has prepared it
            store = PostgresStore.connect(url)
            b = Brain(settings, sims, PostgresIndex.connect(url), store, None, TemplateGenerator(), MemoryAuditStore())
            state["brain"], state["consumer"] = b, OutboxConsumer(store, b.resolver, b.pdp)
        return state["brain"]

    def enforced(email: str, doc_id: str) -> bool:
        b = brain()
        state["consumer"].drain()
        p = personas[email]
        answer = b.ask(Principal(email, tuple(p["roles"]), p["display_name"]), AskRequest(QUESTIONS[doc_id]))
        return doc_id not in [c["doc_id"] for c in answer["citations"]]

    print(json.dumps(run(args.rounds, enforced), indent=2))


if __name__ == "__main__":
    main()

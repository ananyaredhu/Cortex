"""Revocation-to-enforcement timing on the simulators: Workstream A's stages, with a hook for B's.

    python -m connectors.ingestion.revocation_timing              # 5 rounds of each scenario
    python -m connectors.ingestion.revocation_timing --rounds 10

Each round applies one revocation through a simulator's admin API, times each stage from that moment, then undoes
it. The stages:
- `check_access`: until the live `check_access` denies. B's pipeline re-checks every candidate this way, so this is
  what enforces the revocation on the very next query.
- `resolve_identity` (membership revocations): until the person's identity no longer holds the token.
- `outbox`: until an ingestion pass, started right after the revocation, has written the `principal_change` or
  `acl_change` that B's caches act on. A polled source adds up to its poll interval before that pass starts.
- `index` (document ACL changes): until the document's index tokens no longer admit the person.
- `enforced` (optional, B's): `measure(..., enforced=...)` takes a callable (persona email, doc_id) -> bool that is
  True once B's API refuses; for example one asking `/v1/ask` as that persona.

Scenarios: Dana leaves Confluence's security-team (loses the Q3 breach report); the ENG auth decision page is
restricted to payments-eng (Jordan loses it); Priya leaves DBMIG's developer role in Jira (loses DBMIG-142); PAYINC-10
gets a security level only Maya is in (Priya loses it).

Needs the Confluence and Jira simulators (CONFLUENCE_SIM_URL, JIRA_SIM_URL, default 127.0.0.1:8101/8102). Ingestion
writes to its own database, TIMING_DATABASE_URL (default `brain_timing`), so test events never reach the demo outbox.
Every revocation is undone even when a round fails; the `timing` security level it defines in PAYINC stays, empty of
issues. Prints persona names, fixture doc IDs and timings only.
"""
import argparse
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from connectors.env import load_dotenv
from connectors.ingestion.embedding import NullEmbedder
from connectors.ingestion.freshness import percentile
from connectors.ingestion.pg_store import PostgresStore
from connectors.ingestion.pipeline import Ingestor
from connectors.ingestion.scratch_db import prepare
from simulators.confluence import ConfluenceConnector
from simulators.jira import JiraConnector

DEFAULT_URL = "postgresql://brain:brain@localhost:5432/brain_timing"
POLL = 0.02       # seconds between checks of a stage
TIMEOUT = 30.0    # seconds before a stage counts as not reached
RESTRICT = "/sim/admin/pages/auth-service-decision/restrictions"


@dataclass
class Scenario:
    name: str
    source: str                     # confluence | jira
    kind: str                       # principal_change | acl_change
    persona: str                    # canonical email of the person who loses access
    doc_id: str
    token: str | None               # principal_change: the token they lose
    revoke: Callable[[httpx.Client], None]
    restore: Callable[[httpx.Client], None]


def _ok(response: httpx.Response) -> None:
    response.raise_for_status()


def scenarios(jira: httpx.Client) -> list[Scenario]:
    developers = jira.get("/rest/api/2/project/DBMIG/role/developer").raise_for_status().json()["actors"]
    users = sorted(a["displayName"] for a in developers if a.get("actorUser"))
    groups = sorted(a["name"] for a in developers if a.get("actorGroup") or a["type"].endswith("group-role-actor"))
    return [
        Scenario("confluence membership: Dana leaves security-team", "confluence", "principal_change",
                 "dana@companya.com", "confluence:SEC/q3-breach-report", "group:confluence:security-team",
                 lambda c: _ok(c.delete("/sim/admin/groups/security-team/members/dana@companya.com")),
                 lambda c: _ok(c.put("/sim/admin/groups/security-team/members/dana@companya.com"))),
        Scenario("confluence page restriction: ENG decision to payments-eng", "confluence", "acl_change",
                 "jordan@companya.com", "confluence:ENG/auth-service-decision", None,
                 lambda c: _ok(c.put(RESTRICT, json={"groups": ["payments-eng"], "users": []})),
                 lambda c: _ok(c.put(RESTRICT, json={"groups": [], "users": []}))),
        Scenario("jira role: Priya leaves DBMIG developer", "jira", "principal_change",
                 "priya@companya.com", "jira:DBMIG-142", "role:DBMIG:developer",
                 lambda c: _ok(c.delete("/sim/admin/projects/DBMIG/roles/developer/users/priya@companya.com")),
                 lambda c: _ok(c.put("/sim/admin/projects/DBMIG/roles/developer", json={"users": users, "groups": groups}))),
        Scenario("jira security level: PAYINC-10 to Maya only", "jira", "acl_change",
                 "priya@companya.com", "jira:PAYINC-10", None,
                 lambda c: (_ok(c.put("/sim/admin/projects/PAYINC/securitylevels/timing", json={"users": ["maya@companya.com"]})),
                            _ok(c.put("/sim/admin/issues/PAYINC-10/security", json={"level": "timing"}))),
                 lambda c: _ok(c.put("/sim/admin/issues/PAYINC-10/security", json={"level": None}))),
    ]


def _until(condition: Callable[[], bool], started: float) -> float | None:
    """Seconds from `started` until `condition()` holds, or None after TIMEOUT."""
    while True:
        if condition():
            return time.perf_counter() - started
        if time.perf_counter() - started > TIMEOUT:
            return None
        time.sleep(POLL)


def measure(scenario: Scenario, connector, admin: httpx.Client, store: PostgresStore, ingestor: Ingestor,
            enforced: Callable[[str, str], bool] | None = None) -> dict[str, float | None]:
    identity = connector.resolve_identity(scenario.persona)
    if identity is None or not connector.check_access(identity, scenario.doc_id).allowed:
        raise RuntimeError(f"{scenario.name}: the persona cannot read the document before the revocation")
    ingestor.run_source(connector)                       # catch up, so the pass below sees only this revocation
    last_seq = max([e.seq for e in store.events_after(0, 100_000)] or [0])
    held = set(identity.groups)
    out: dict[str, float | None] = {}
    try:
        started = time.perf_counter()
        scenario.revoke(admin)
        out["check_access"] = _until(lambda: not connector.check_access(identity, scenario.doc_id).allowed, started)
        if scenario.kind == "principal_change":
            out["resolve_identity"] = _until(
                lambda: scenario.token not in ((connector.resolve_identity(scenario.persona) or identity).groups), started)
        ingestor.run_source(connector, trigger="event")

        def outbox() -> bool:
            return any((e.kind == scenario.kind and (e.doc_id == scenario.doc_id if e.kind == "acl_change"
                                                     else e.principal == f"user:{scenario.persona}"))
                       for e in store.events_after(last_seq))
        out["outbox"] = _until(outbox, started)
        if scenario.kind == "acl_change":
            out["index"] = _until(lambda: all(not set(c.acl_tokens) & held for c in store.chunks_of(scenario.doc_id)), started)
        if enforced is not None:
            out["enforced"] = _until(lambda: enforced(scenario.persona, scenario.doc_id), started)
    finally:
        scenario.restore(admin)
    ingestor.run_source(connector)                       # and back, so the next round starts from the seed state
    if not connector.check_access(identity, scenario.doc_id).allowed:
        raise RuntimeError(f"{scenario.name}: access did not come back after the restore")
    return out


def _summary(values: list[float | None]) -> dict:
    got = sorted(v for v in values if v is not None)
    ms = lambda v: round(v * 1000, 1)   # noqa: E731
    return {"rounds": len(values), "not_reached": len(values) - len(got),
            "p50_ms": ms(percentile(got, 50)) if got else None, "p95_ms": ms(percentile(got, 95)) if got else None,
            "max_ms": ms(got[-1]) if got else None}


def run(rounds: int, enforced: Callable[[str, str], bool] | None = None) -> dict:
    url = os.environ.get("TIMING_DATABASE_URL") or DEFAULT_URL
    prepare(url, prefix="brain_timing")
    admins = {"confluence": httpx.Client(base_url=os.environ.get("CONFLUENCE_SIM_URL") or "http://127.0.0.1:8101", timeout=10),
              "jira": httpx.Client(base_url=os.environ.get("JIRA_SIM_URL") or "http://127.0.0.1:8102", timeout=10)}
    connectors = {"confluence": ConfluenceConnector(admins["confluence"]), "jira": JiraConnector(admins["jira"])}
    store = PostgresStore.connect(url)
    try:
        ingestor = Ingestor(connectors.values(), store, NullEmbedder())
        ingestor.run_once()                              # crawl (or catch up) before measuring
        result = {}
        for scenario in scenarios(admins["jira"]):
            samples = [measure(scenario, connectors[scenario.source], admins[scenario.source], store, ingestor, enforced)
                       for _ in range(rounds)]
            result[scenario.name] = {stage: _summary([s.get(stage) for s in samples]) for stage in samples[0]}
        return result
    finally:
        store.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m connectors.ingestion.revocation_timing", description=__doc__.splitlines()[0])
    parser.add_argument("--rounds", type=int, default=5)
    args = parser.parse_args()
    load_dotenv()
    print(json.dumps(run(args.rounds), indent=2))


if __name__ == "__main__":
    main()

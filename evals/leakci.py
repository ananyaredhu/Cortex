"""Leak-CI: metamorphic noninterference tests (Workstream C).

A hidden document must make no difference to what its non-readers get. For each case in evals/leakci_cases.json
the runner asks a question as a persona, then plants a document on the same topic that the persona may not see,
lets ingestion pick it up, and asks again; then edits it (new text, new canary) and asks again; then removes it and
asks again. Every later answer is compared with the first:

- a **leak** is the planted document's canary or id anywhere in a response, or the document cited;
- **unstable** is any other difference (refusal flag, cited set, per-source `coverage.shown`, or the refusal text),
  reported in its own suite but not counted as a leak, because a nondeterministic generator can change an answer
  without any hidden document being involved. The question is asked twice before the plant: when those two answers
  already differ, a later difference proves nothing and the check passes with an "inconclusive" note;
- the **timing band**: a refusal after the plant must stay within max(1 s, the baseline refusal's latency) of it.
  The baseline is the second ask, so a cold start does not widen the band;
- **non-vacuity**: before comparing, the runner checks that the document is really hidden from the asker and
  really in the index carrying its canary (A's `verify`), so a run cannot pass because nothing was planted;
- a **control** case plants a document the persona may see, carrying a `fact` no other document states, and asks
  for that fact: the planted document must be cited after the plant and the edit and gone after the removal. That
  proves the plant reaches the index and the answer, without depending on which of several documents a model
  prefers to cite.

Targets: `--target fixture` runs the real pipeline over the fixture corpus in this process (no database, no model;
what CI runs, see evals/tests/test_leakci.py). `--target http://host:port` runs against a served Brain whose
Confluence and Jira are the simulators: documents are planted through `simulators/leakci.py` and ingested with one
pass of the ingestion pipeline into the Brain's Postgres (`DATABASE_URL`; `EMBEDDING_BACKEND=none` keeps bge-m3 out
of this process on a small machine, the keyword leg still finds the planted documents). Planted documents are
removed again at the end, whatever happens. The runner signs in through the target's mock IdP (BRAIN_MOCK_IDP=1) and
falls back to the development login (BRAIN_DEV_AUTH=1); the target needs one of the two.

The result goes to evals/scoreboard/leakci-latest.json (`--out`), which `GET /v1/leakci/latest` serves to the Admin
page: {as_of, target, cases, leaks, suites: [{name, category, passed, failed, last_run_at}], details}. Exit code 1
when anything leaked or failed. Prints doc ids, canaries and persona ids only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from fixtures.loader import load, persona_by_id

ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = ROOT / "evals" / "leakci_cases.json"
SCOREBOARD_PATH = ROOT / "evals" / "scoreboard" / "leakci-latest.json"
CANARY = re.compile(r"LEAKCI-CANARY-[0-9a-f]{16}")
TIMING_SLACK_MS = 1000

SUITES = {                     # name -> category, in display order
    "Hidden document planted": "noninterference",
    "Hidden document edited": "noninterference",
    "Hidden document removed": "noninterference",
    "Canary strings": "leak",
    "Refusal timing band": "side-channel",
    "Answer stability": "stability",
    "Non-vacuity (hidden and indexed)": "control",
    "Control document is seen": "control",
}
STEPS = ("planted", "edited", "removed")


@dataclass
class Planted:
    doc_id: str
    canary: str


class Planter(Protocol):
    """Where hidden documents go. Both implementations mirror simulators/leakci.py's verbs."""

    def plant(self, source: str, topic: str, *, mode: str, visible_to: tuple[str, ...], fact: str | None = None) -> Planted: ...
    def edit(self, doc_id: str, *, topic: str, fact: str | None = None) -> Planted: ...
    def remove(self, doc_id: str) -> None: ...
    def ingest(self) -> None: ...
    def verify(self, doc_id: str, asker: str) -> dict: ...   # {"hidden_from_asker", "indexed", "index_has_canary", "ok"}


# -- asking and comparing ------------------------------------------------------------------------
@dataclass
class Asked:
    response: dict
    latency_ms: int

    @property
    def cited(self) -> set[str]:
        return {c["doc_id"] for c in self.response.get("citations", [])}

    @property
    def shown(self) -> dict[str, int]:
        return {s: v.get("shown", 0) for s, v in (self.response.get("coverage") or {}).items()}


_TOKENS: dict[tuple[int, str], str] = {}   # (client, persona) -> bearer token, for the run


def bearer(client, persona: str, *, fresh: bool = False) -> str:
    """A token for `persona`: signed by the mock IdP when the target serves one (`POST /idp/token`), else the
    development login (`dev:<persona>`, which a Brain accepts only with BRAIN_DEV_AUTH=1)."""
    key = (id(client), persona)
    if fresh or key not in _TOKENS:
        r = client.post("/idp/token", json={"persona": persona})
        _TOKENS[key] = r.json()["access_token"] if r.status_code == 200 else f"dev:{persona}"
    return _TOKENS[key]


def ask(client, persona: str, question: str) -> Asked:
    r, latency = None, 0
    for fresh in (False, True):          # a mock IdP token lasts 15 minutes; a run with a model can outlive it
        headers = {"Authorization": f"Bearer {bearer(client, persona, fresh=fresh)}"}   # signed in before the clock starts
        t0 = time.monotonic()
        r = client.post("/v1/ask", json={"question": question}, headers=headers)
        latency = int((time.monotonic() - t0) * 1000)
        if r.status_code != 401:
            break
    if r.status_code != 200:
        raise RuntimeError(f"/v1/ask returned {r.status_code} for {persona}")
    return Asked(r.json(), latency)


def leaks_in(response: dict, planted: list[Planted]) -> list[str]:
    """What of the planted documents shows in `response`: canaries (old or new), ids, citations."""
    dump = json.dumps(response)
    found = [f"canary {c}" for c in CANARY.findall(dump)]
    for p in planted:
        if p.doc_id in dump:
            found.append(f"doc id {p.doc_id}")
    return found


def instabilities(before: Asked, after: Asked) -> list[str]:
    out = []
    if before.response.get("refused") != after.response.get("refused"):
        out.append(f"refused {before.response.get('refused')} -> {after.response.get('refused')}")
    if before.cited != after.cited:
        out.append(f"cited {sorted(before.cited)} -> {sorted(after.cited)}")
    if before.shown != after.shown:
        out.append(f"coverage.shown {before.shown} -> {after.shown}")
    if before.response.get("refused") and before.response.get("answer") != after.response.get("answer"):
        out.append("refusal text changed")
    return out


def within_band(before: Asked, after: Asked) -> bool:
    return abs(after.latency_ms - before.latency_ms) <= max(TIMING_SLACK_MS, before.latency_ms)


# -- one case --------------------------------------------------------------------------------------
@dataclass
class Check:
    suite: str
    ok: bool
    note: str = ""


@dataclass
class CaseResult:
    case: str
    persona: str
    control: bool
    checks: list[Check] = field(default_factory=list)
    latency_ms: dict[str, int] = field(default_factory=dict)
    error: str | None = None

    @property
    def leaks(self) -> int:
        return sum(1 for c in self.checks if not c.ok and SUITES[c.suite] in ("noninterference", "leak"))


def run_case(client, case: dict, planter: Planter) -> CaseResult:
    persona, question, control = case["persona"], case["question"], bool(case.get("control"))
    result = CaseResult(case["id"], persona, control)
    checks, planted = result.checks, []
    warmup = ask(client, persona, question)       # a cold first ask (models, caches) would widen the timing band
    baseline = ask(client, persona, question)
    result.latency_ms.update(warmup=warmup.latency_ms, baseline=baseline.latency_ms)
    noise = instabilities(before=warmup, after=baseline)   # the answer already varies with nothing planted
    doc_id = None
    try:
        first = planter.plant(case["source"], case["topic"], mode=case["mode"], visible_to=tuple(case.get("visible_to", ())),
                              fact=case.get("fact"))
        doc_id, planted = first.doc_id, [first]
        for step in STEPS:
            if step == "edited":
                planted.append(planter.edit(doc_id, topic=case["topic"], fact=case.get("fact")))
            elif step == "removed":
                planter.remove(doc_id)
            planter.ingest()
            if step != "removed":
                v = planter.verify(doc_id, persona)
                if control:
                    checks.append(Check("Non-vacuity (hidden and indexed)", bool(v.get("indexed") and v.get("index_has_canary")),
                                        f"{step}: control indexed={v.get('indexed')} canary={v.get('index_has_canary')}"))
                else:
                    checks.append(Check("Non-vacuity (hidden and indexed)", bool(v.get("ok")), f"{step}: {v}"))
            after = ask(client, persona, question)
            result.latency_ms[step] = after.latency_ms
            if control:
                seen = doc_id in after.cited
                checks.append(Check("Control document is seen", seen if step != "removed" else not seen,
                                    f"{step}: cited={seen}"))
                continue
            leaked = leaks_in(after.response, planted)
            suite = f"Hidden document {step}"
            checks.append(Check(suite, not leaked, f"{step}: {'; '.join(leaked) or 'no trace'}"))
            checks.append(Check("Canary strings", not any(x.startswith("canary") for x in leaked), step))
            unstable = instabilities(before=baseline, after=after)
            if unstable and noise:
                checks.append(Check("Answer stability", True, f"{step}: inconclusive, the answer varied before the plant "
                                    f"({'; '.join(noise)}); after it: {'; '.join(unstable)}"))
            else:
                checks.append(Check("Answer stability", not unstable, f"{step}: {'; '.join(unstable) or 'same'}"))
            if baseline.response.get("refused"):
                checks.append(Check("Refusal timing band", within_band(baseline, after),
                                    f"{step}: {baseline.latency_ms} ms -> {after.latency_ms} ms"))
    except Exception as exc:  # noqa: BLE001 - the case is reported as failed, the run goes on
        result.error = f"{type(exc).__name__}: {exc}"
        checks.append(Check("Non-vacuity (hidden and indexed)", False, result.error))
    finally:
        if doc_id is not None:
            try:
                planter.remove(doc_id)
                planter.ingest()
            except Exception:  # noqa: BLE001 - already removed, or the simulator is gone
                pass
    return result


# -- the run and its scoreboard --------------------------------------------------------------------
def run_all(client, cases: list[dict], planter: Planter, *, target: str) -> dict:
    results = [run_case(client, case, planter) for case in cases]
    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    suites = []
    for name, category in SUITES.items():
        checks = [c for r in results for c in r.checks if c.suite == name]
        if checks:
            suites.append({"name": name, "category": category, "passed": sum(c.ok for c in checks),
                           "failed": sum(not c.ok for c in checks), "last_run_at": now})
    return {
        "as_of": now, "target": target, "cases": len(results), "leaks": sum(r.leaks for r in results),
        "failed": sum(1 for r in results for c in r.checks if not c.ok), "suites": suites,
        "details": [asdict(r) for r in results],
    }


def write_scoreboard(board: dict, path: Path = SCOREBOARD_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(board, indent=1), encoding="utf8")
    return path


def load_cases(path: Path = CASES_PATH) -> list[dict]:
    return json.loads(path.read_text(encoding="utf8"))["cases"]


# -- planters --------------------------------------------------------------------------------------
def _canary() -> str:
    return f"LEAKCI-CANARY-{secrets.token_hex(8)}"


def _body(topic: str, canary: str, fact: str | None = None) -> str:
    return (f"{topic}. Reference {canary}. {fact + ' ' if fact else ''}Summary of {topic}: the owner's notes, the "
            f"decisions taken and the figures behind them. Reference {canary}.")


class FixturePlanter:
    """Plants into a fixture runtime's corpus (brain.runtime.fixture_runtime) through its FixtureConnectors.

    Hidden documents get a holders token nobody has (`group:<source>:leakci-<suffix>`); `visible_to` adds the
    personas' own `user:` tokens, which the identity resolver gives every asker. `mode` only picks the container,
    since the fixture connector's permission rule is token overlap: `space` / `project` make one of their own,
    `restricted` / `level` sit inside ENG / PAYINC, which other people can see.
    """

    def __init__(self, runtime) -> None:
        self._runtime = runtime
        self._data = load()

    def _email(self, persona: str) -> str:
        return persona_by_id(self._data, persona)["email"]

    def plant(self, source: str, topic: str, *, mode: str, visible_to: tuple[str, ...], fact: str | None = None) -> Planted:
        suffix = secrets.token_hex(4)
        canary = _canary()
        if source == "confluence":
            space = "LK" + suffix.upper() if mode == "space" else "ENG"
            doc_id, kind, parent = f"confluence:{space}/leakci-{suffix}", "page", f"confluence:{space}"
        elif source == "jira":
            project = "LK" + suffix.upper() if mode == "project" else "PAYINC"
            doc_id, kind, parent = f"jira:{project}-{1000 + int(suffix, 16) % 9000}", "issue", f"jira:{project}"
        else:
            raise ValueError("the fixture planter plants Confluence pages and Jira issues")
        tokens = [f"group:{source}:leakci-{suffix}"] + [f"user:{self._email(p)}" for p in visible_to]
        now = self._data["now"]
        doc = {
            "doc_id": doc_id, "source": source, "kind": kind, "title": f"{topic} ({canary[-6:]})",
            "url": f"https://fixtures.invalid/{source}/leakci-{suffix}", "body": _body(topic, canary, fact), "parent_id": parent,
            "links": [], "author": "leakci@companya.com", "created_at": now, "updated_at": now, "version": f"{now}#1",
            "tags": ["leakci"],
            "acl": {"tokens": tokens, "native": {"holders": f"leakci-{suffix}", "mode": mode},
                    "snapshot_hash": f"sha256:leakci-{suffix}-1", "observed_at": now},
        }
        self._runtime.brain.connectors[source].upsert(doc)
        return Planted(doc_id, canary)

    def edit(self, doc_id: str, *, topic: str, fact: str | None = None) -> Planted:
        source = doc_id.split(":", 1)[0]
        connector = self._runtime.brain.connectors[source]
        doc = dict(connector.state.docs[doc_id])
        canary = _canary()
        n = int(doc["version"].rsplit("#", 1)[1]) + 1
        doc.update(title=f"{topic} ({canary[-6:]})", body=_body(topic, canary, fact), version=f"{doc['version'].rsplit('#', 1)[0]}#{n}")
        doc["acl"] = {**doc["acl"], "snapshot_hash": f"{doc['acl']['snapshot_hash'].rsplit('-', 1)[0]}-{n}"}
        connector.upsert(doc)
        return Planted(doc_id, canary)

    def remove(self, doc_id: str) -> None:
        source = doc_id.split(":", 1)[0]
        connector = self._runtime.brain.connectors[source]
        if doc_id in connector.state.docs:
            connector.delete(doc_id)

    def ingest(self) -> None:
        self._runtime.ingest()

    def verify(self, doc_id: str, asker: str) -> dict:
        email = self._email(asker)
        allowed = self._runtime.brain.evaluate(email, doc_id)["allowed"]
        chunks = self._runtime.brain.store.chunks_of(doc_id)
        held = set(persona_by_id(self._data, asker)["tokens"])
        out = {"doc_id": doc_id, "asker": email, "hidden_from_asker": not allowed, "indexed": bool(chunks),
               "index_has_canary": any(CANARY.search(c.text) for c in chunks),
               "index_tokens_exclude_asker": bool(chunks) and not any(set(c.acl_tokens) & held for c in chunks)}
        out["ok"] = all(v for k, v in out.items() if k not in ("doc_id", "asker"))
        return out


class SimulatorPlanter:
    """A's `simulators/leakci.py` for the planting, one ingestion pass over the two simulators for the index."""

    def __init__(self, leakci, store, embedder, connectors) -> None:
        from connectors.ingestion.pipeline import Ingestor

        self._leakci = leakci
        self._store = store
        self._ingestor = Ingestor(connectors, store, embedder)
        # Catch up before anything is planted. After a simulator restart the stored cursor has expired and the next
        # pass is a full crawl, which lists documents but no membership changes: a control's holder would then keep
        # the identity the Brain cached before the plant (identity_ttl_s) and not find the planted document.
        self._ingestor.run_once()

    @classmethod
    def from_env(cls) -> SimulatorPlanter:
        from connectors.env import load_dotenv
        from connectors.ingestion.embedding import from_env as embedder_from_env
        from connectors.ingestion.pg_store import DEFAULT_URL, PostgresStore
        from simulators.confluence import ConfluenceConnector
        from simulators.jira import JiraConnector
        from simulators.leakci import LeakCI

        load_dotenv()
        store = PostgresStore.connect(os.environ.get("DATABASE_URL") or DEFAULT_URL)
        connectors = [ConfluenceConnector.from_url(os.environ.get("CONFLUENCE_SIM_URL") or "http://localhost:8101"),
                      JiraConnector.from_url(os.environ.get("JIRA_SIM_URL") or "http://localhost:8102")]
        return cls(LeakCI.from_env(), store, embedder_from_env(), connectors)

    def plant(self, source: str, topic: str, *, mode: str, visible_to: tuple[str, ...], fact: str | None = None) -> Planted:
        p = self._leakci.plant(source, topic, mode=mode, visible_to=visible_to, fact=fact)
        return Planted(p.doc_id, p.canary)

    def edit(self, doc_id: str, *, topic: str, fact: str | None = None) -> Planted:
        p = self._leakci.edit(doc_id, topic=topic, fact=fact)
        return Planted(p.doc_id, p.canary)

    def remove(self, doc_id: str) -> None:
        self._leakci.remove(doc_id)

    def ingest(self) -> None:
        self._ingestor.run_once()

    def verify(self, doc_id: str, asker: str) -> dict:
        return self._leakci.verify(doc_id, asker, index=self._store)


# -- CLI -------------------------------------------------------------------------------------------
def _fixture_target() -> tuple[Any, FixturePlanter]:
    from fastapi.testclient import TestClient

    from brain.api.app import create_app
    from brain.runtime import fixture_runtime

    runtime = fixture_runtime()
    return TestClient(create_app(runtime)), FixturePlanter(runtime)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.leakci", description=__doc__.splitlines()[0])
    parser.add_argument("--target", default="fixture", help="fixture (in this process) or the URL of a served Brain "
                        "whose Confluence and Jira are the simulators (default: fixture)")
    parser.add_argument("--cases", type=Path, default=CASES_PATH)
    parser.add_argument("--out", type=Path, default=SCOREBOARD_PATH)
    parser.add_argument("--only", help="run one case by id")
    args = parser.parse_args(argv)

    cases = load_cases(args.cases)
    if args.only:
        cases = [c for c in cases if c["id"] == args.only]
    if args.target == "fixture":
        client, planter = _fixture_target()
    else:
        import httpx

        client, planter = httpx.Client(base_url=args.target, timeout=180.0), SimulatorPlanter.from_env()
    board = run_all(client, cases, planter, target=args.target)
    path = write_scoreboard(board, args.out)
    for r in board["details"]:
        bad = [c for c in r["checks"] if not c["ok"]]
        print(f"{'FAIL' if bad else 'ok  '} {r['case']} ({r['persona']}){' control' if r['control'] else ''}"
              + "".join(f"\n       {c['suite']}: {c['note']}" for c in bad))
    print(f"{board['leaks']} leaks, {board['failed']} failed checks in {board['cases']} cases; wrote {path}")
    return 1 if board["leaks"] or board["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())

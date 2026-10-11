"""Golden-case runner. Runs the cases in fixtures/company_a.json against any API that follows
docs/02-contracts/api.md. Used by the stub API tests now and by CI against the real system later.

`client` is anything with httpx-style `.post(path, json=..., headers=...)` (starlette TestClient or httpx.Client).
`advance(event_id)` applies a scripted change (stub: POST /sim/advance; real: simulator admin endpoint).
`ids` translates the real system's Slack and Drive document IDs back to the fixture IDs the cases use
(`SeedIds.from_manifests()`); the default, for the stub API, leaves IDs as they are. See evals/ids.py.
"""
import argparse
import json
import sys
import time
from collections.abc import Callable

from evals.ids import SeedIds


def _headers(persona_id: str) -> dict:
    return {"Authorization": f"Bearer dev:{persona_id}"}   # real system: a JWT from the mock IdP


def _check(resp: dict, rules: dict) -> list[str]:
    errs = []
    cited = {c["doc_id"] for c in resp.get("citations", [])}
    text = resp.get("answer", "").lower()
    dump = json.dumps(resp).lower()
    if "refused" in rules and resp.get("refused") is not rules["refused"]:
        errs.append(f"refused={resp.get('refused')} expected {rules['refused']}")
    for d in rules.get("must_cite", []):
        if d not in cited:
            errs.append(f"missing citation {d}")
    for d in rules.get("must_not_cite", []):
        if d in cited:
            errs.append(f"forbidden citation {d}")
    for s in rules.get("must_contain", []):
        if s.lower() not in text:
            errs.append(f"answer lacks {s!r}")
    for s in rules.get("must_not_contain", []):
        if s.lower() in dump:
            errs.append(f"response contains forbidden {s!r}")
    return errs


def run_case(client, case: dict, advance, ids: SeedIds | None = None, headers: Callable[[str], dict] = _headers) -> list[str]:
    ids = ids or SeedIds.fixture()
    ids.require([case])

    def ask() -> dict:
        resp = client.post("/v1/ask", json={"question": case["question"]}, headers=headers(case["persona"])).json()
        return ids.response_in_fixture_ids(resp)

    errs: list[str] = []
    if "after_event" in case:
        errs += [f"before: {e}" for e in _check(ask(), case.get("before_event", {}))]
        advance(case["after_event"])
        errs += [f"after: {e}" for e in _check(ask(), case.get("after", {}))]
    else:
        errs += _check(ask(), case)
    return errs


def main(argv: list[str] | None = None, client=None) -> int:
    """`python -m evals.golden --target http://localhost:8000`: the golden cases against a served Brain (the real stack too).

    Signs in through the target's mock IdP (else the development login), translates Slack and Drive IDs through the seed manifests,
    and pauses between cases so ADP's rate limit is not tripped. A case with a scripted change (`after_event`: the runbook edit,
    Priya leaving a channel) needs the simulator admin steps or a Slack hand step on a real stack, so it is listed to do by hand
    unless the target serves `/sim/advance` (the fixture runtime) and `--include-scripted` is given."""
    from evals.leakci import bearer
    from fixtures.loader import load

    parser = argparse.ArgumentParser(prog="python -m evals.golden", description=main.__doc__.split("\n")[0])
    parser.add_argument("--target", default="http://localhost:8000")
    parser.add_argument("--only", help="run one case by id")
    parser.add_argument("--include-scripted", action="store_true", help="also run the cases with a scripted change (needs /sim/advance)")
    parser.add_argument("--pause", type=float, default=11.0, help="seconds between cases, to stay under ADP's rate (default 11)")
    args = parser.parse_args(argv)
    if client is None:
        import httpx
        client = httpx.Client(base_url=args.target, timeout=180.0)
    try:
        ids = SeedIds.from_manifests()
        ids.require(load()["golden"])
    except Exception as exc:  # noqa: BLE001 - no manifests, or they do not cover the cases: fixture IDs only, said out loud
        print(f"note: using fixture IDs ({type(exc).__name__}); Slack and Drive citations from a real stack will not match")
        ids = SeedIds.fixture()
    cases = [c for c in load()["golden"] if not args.only or c["id"] == args.only]
    scripted = [c for c in cases if "after_event" in c]
    can_advance = bool(scripted) and client.post("/sim/reset").status_code == 200     # only the fixture runtime serves the scripted events
    todo = [c for c in cases if c not in scripted or (args.include_scripted and can_advance)]
    headers = lambda persona: {"Authorization": f"Bearer {bearer(client, persona)}"}   # noqa: E731
    advance = (lambda event: client.post("/sim/advance", json={"event_id": event})) if can_advance else (lambda event: None)
    failed = 0
    for n, case in enumerate(todo):
        if n and args.pause:
            time.sleep(args.pause)
        if args.include_scripted and can_advance and "after_event" in case:
            client.post("/sim/reset")
        errs = run_case(client, case, advance, ids, headers)
        failed += bool(errs)
        print(f"{'FAIL' if errs else 'pass'}  {case['id']} ({case['persona']})" + "".join(f"\n        {e}" for e in errs))
    for case in scripted:
        if case not in todo:
            print(f"hand  {case['id']}: needs the change '{case['after_event']}' (see docs/04-scenarios.md); do it by hand")
    print(f"{len(todo) - failed} passed, {failed} failed, {len([c for c in scripted if c not in todo])} left to do by hand")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())


def run_all(client, cases: list[dict], advance, reset, ids: SeedIds | None = None) -> dict[str, list[str]]:
    """Refuses to start (`UnmappedDocuments`) if `ids` cannot translate a document the cases name."""
    ids = ids or SeedIds.fixture()
    ids.require(cases)
    results = {}
    for case in cases:
        reset()
        results[case["id"]] = run_case(client, case, advance, ids)
    return results

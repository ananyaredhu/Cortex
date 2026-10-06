"""Golden-case runner. Runs the cases in fixtures/company_a.json against any API that follows
docs/02-contracts/api.md. Used by the stub API tests now and by CI against the real system later.

`client` is anything with httpx-style `.post(path, json=..., headers=...)` (starlette TestClient or httpx.Client).
`advance(event_id)` applies a scripted change (stub: POST /sim/advance; real: simulator admin endpoint).
`ids` translates the real system's Slack and Drive document IDs back to the fixture IDs the cases use
(`SeedIds.from_manifests()`); the default, for the stub API, leaves IDs as they are. See evals/ids.py.
"""
import json

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


def run_case(client, case: dict, advance, ids: SeedIds | None = None) -> list[str]:
    ids = ids or SeedIds.fixture()
    ids.require([case])

    def ask() -> dict:
        resp = client.post("/v1/ask", json={"question": case["question"]}, headers=_headers(case["persona"])).json()
        return ids.response_in_fixture_ids(resp)

    errs: list[str] = []
    if "after_event" in case:
        errs += [f"before: {e}" for e in _check(ask(), case.get("before_event", {}))]
        advance(case["after_event"])
        errs += [f"after: {e}" for e in _check(ask(), case.get("after", {}))]
    else:
        errs += _check(ask(), case)
    return errs


def run_all(client, cases: list[dict], advance, reset, ids: SeedIds | None = None) -> dict[str, list[str]]:
    """Refuses to start (`UnmappedDocuments`) if `ids` cannot translate a document the cases name."""
    ids = ids or SeedIds.fixture()
    ids.require(cases)
    results = {}
    for case in cases:
        reset()
        results[case["id"]] = run_case(client, case, advance, ids)
    return results

"""HTTP face of a simulated Slack, for scale runs: the Web API methods the Slack connector calls, answered by the
in-memory Slack the connector's tests use (connectors/slack/fake.py), so the real connector runs unchanged.

    SIM_SEED=scale uvicorn simulators.slack.app:app --port 8103      (Company A plus 220 generated channels)
    uvicorn simulators.slack.app:app --port 8103                     (Company A only)

- `POST /api/<method>`: the Slack Web API, form-encoded, as the connector sends it. No token is checked.
- `GET /sim/identity-map`: the identity map for this workspace ({canonical email: {"slack": address}}), which a
  real workspace keeps in `identity-map.local.json`. Simulator-only.

Read-only: no admin endpoints, no events. State is built at start-up and never changes.
"""
import os

import httpx
from fastapi import FastAPI, Request, Response

from connectors.slack.fake import FakeSlack, seed_company_a
from fixtures.loader import load

SEEDS = ("company_a", "scale")


def build(seed: str, scale=None) -> tuple[FakeSlack, dict[str, dict[str, str]]]:
    """The workspace and its identity map. `scale` (simulators.scale.Scale) defaults to SCALE_* from the environment."""
    if seed not in SEEDS:
        raise ValueError(f"SIM_SEED must be one of {', '.join(SEEDS)}")
    fake = FakeSlack()
    identities, _ = seed_company_a(fake, load())
    accounts = {email: {"slack": identities.platform_account("slack", email)} for email in identities.canonical_emails("slack")}
    if seed == "scale":
        from simulators.scale import Scale, generate, seed_slack
        seed_slack(fake, generate(scale or Scale.from_env()), accounts)
    return fake, accounts


def create_app(fake: FakeSlack, accounts: dict[str, dict[str, str]]) -> FastAPI:
    app = FastAPI(title="Slack simulator", version="0.1")

    @app.post("/api/{method}")
    async def call(method: str, request: Request) -> Response:
        body = await request.body()
        answer = fake._handle(httpx.Request("POST", f"https://slack.test/api/{method}", content=body,
                                            headers={"content-type": "application/x-www-form-urlencoded"}))
        headers = {k: v for k, v in answer.headers.items() if k.lower() == "retry-after"}
        return Response(answer.content, status_code=answer.status_code, headers=headers, media_type="application/json")

    @app.get("/sim/identity-map")
    def identity_map() -> dict:
        return accounts

    return app


app = create_app(*build(os.environ.get("SIM_SEED") or "company_a"))

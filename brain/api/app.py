"""FastAPI front door over the pipeline (api.md 0.2). The MCP server wraps the same `Runtime`.

Every handler authenticates, syncs the outbox, then calls the Brain. Refusals are 200s with the uniform shape.
"""
import contextlib
import json
import queue
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from brain.auth import Authenticator, AuthError, Principal, mint_token
from brain.config import ADMIN_ROLES, COMPLIANCE_ROLE, POLICY_VERSION
from brain.pipeline.graph import AskRequest
from brain.runtime import Runtime
from fixtures.loader import load

REPO_ROOT = Path(__file__).resolve().parents[2]


class AskBody(BaseModel):
    question: str
    conversation_id: str | None = None
    skill_hint: str | None = None
    sources: list[str] | None = None
    time_range: str | None = None


class EvaluateBody(BaseModel):
    user: str
    doc_id: str


class AdvanceBody(BaseModel):
    event_id: str
    ingest: bool = True


class TokenBody(BaseModel):
    persona: str


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def create_app(runtime: Runtime) -> FastAPI:
    settings = runtime.settings
    auth = Authenticator(signing_key=settings.jwt_signing_key, audience=settings.jwt_audience, dev_auth=settings.dev_auth)
    # MCP server (ADR-004), only when asked for (BRAIN_MCP=1). Its session manager must start and stop with this app.
    mcp_app = None
    lifespan = None
    if settings.mcp:
        from mcp_server.server import build_mcp_app  # imported late: the SDK is only needed when MCP is on
        mcp_app = build_mcp_app(runtime, auth)

        @contextlib.asynccontextmanager
        async def lifespan(_app: FastAPI):
            async with mcp_app.router.lifespan_context(mcp_app):
                yield

    app = FastAPI(title="Internal Brain API", version="0.2", lifespan=lifespan)
    suggestions = {p["email"]: [g["question"] for g in load()["golden"] if g["persona"] == p["id"]][:3]
                   for p in load()["personas"]}

    # -- mock IdP (fix F2): a demo sign-in that issues short-lived signed tokens for the fictional personas ----------
    if settings.mock_idp:
        if not settings.jwt_signing_key:
            raise ValueError("BRAIN_MOCK_IDP=1 needs JWT_SIGNING_KEY")
        personas = {p["id"]: p for p in load()["personas"]}

        @app.post("/idp/token")
        def idp_token(body: TokenBody):
            """Pick a persona, get a token. There is no password: this stands in for "Sign in with ..." in the demo and
            only knows the fictional Company A personas. A real IdP replaces this endpoint and nothing else."""
            p = personas.get(body.persona)
            if p is None:
                raise HTTPException(404, "unknown persona")
            token = mint_token(settings.jwt_signing_key, settings.jwt_audience, p["email"], p["roles"],
                               name=p["display_name"], ttl_s=settings.mock_idp_ttl_s)
            return {"access_token": token, "token_type": "Bearer", "expires_in": settings.mock_idp_ttl_s,
                    "persona": {"id": p["id"], "display_name": p["display_name"], "email": p["email"], "roles": p["roles"]}}

    def principal(request: Request, authorization: str | None = Header(None)) -> Principal:
        try:
            p = auth.authenticate(authorization, client=request.headers.get("x-brain-client", "ui"))
        except AuthError as exc:
            raise HTTPException(401, str(exc)) from exc
        runtime.sync()
        return p

    Current = Annotated[Principal, Depends(principal)]

    def require(p: Principal, roles) -> None:
        if not p.has_any(roles):
            raise HTTPException(403, f"one of these roles is required: {', '.join(sorted(roles))}")

    def log_admin(p: Principal, what: str, detail: dict) -> None:
        runtime.audit.record({"request_id": f"adm_{runtime.audit_store.count() + 1}", "event_type": "admin_view",
                              "actor": {"user_id": p.email, "roles": list(p.roles), "client": p.client},
                              "query": {"text": what, "skill": None}, "detail": detail, "decisions": [], "flags": []})

    # -- health --------------------------------------------------------------------------------
    @app.get("/v1/health")
    def health():
        return {"ok": True, "stub": False, "sources": sorted(runtime.brain.connectors), "generator": runtime.brain.generator.model,
                "policy_version": POLICY_VERSION}

    # -- ask -----------------------------------------------------------------------------------
    @app.post("/v1/ask")
    def ask(body: AskBody, p: Current):
        return runtime.brain.ask(p, AskRequest(**body.model_dump()))

    @app.post("/v1/ask/stream")
    def ask_stream(body: AskBody, p: Current):
        q: queue.Queue = queue.Queue()

        def run() -> None:
            try:
                result = runtime.brain.ask(p, AskRequest(**body.model_dump()),
                                           on_stage=lambda stage, status: q.put(("stage", {"stage": stage, "status": status})))
                q.put(("result", result))
            except Exception as exc:                        # noqa: BLE001 - surface as an error event
                q.put(("error", {"error": {"code": "pipeline_error", "message": type(exc).__name__}}))
            q.put(None)

        threading.Thread(target=run, daemon=True).start()

        def events() -> Iterator[str]:
            while (item := q.get()) is not None:
                yield _sse(*item)

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.get("/v1/conversations")
    def conversations(p: Current):
        return {"conversations": runtime.brain.conversations(p)}

    @app.get("/v1/conversations/{conversation_id}")
    def conversation(conversation_id: str, p: Current):
        out = runtime.brain.conversation(p, conversation_id)
        if out is None:                                    # someone else's or nonexistent: the same answer
            raise HTTPException(404, "no such conversation")
        return out

    # -- my work, access, alerts --------------------------------------------------------------
    @app.get("/v1/mywork")
    def mywork(p: Current):
        vis = runtime.brain.visible_documents(p)
        return {
            "user": {"display_name": p.display_name or p.email, "roles": list(p.roles)},
            "issues": [{"doc_id": d.doc_id, "title": d.title, "url": d.url, "status": None} for d in vis if d.source == "jira"],
            "projects": sorted({d.parent_id for d in vis if d.source == "jira" and d.parent_id}),
            "channels": sorted({d.parent_id for d in vis if d.source == "slack" and d.parent_id}),
            "recent_pages": [{"doc_id": d.doc_id, "title": d.title, "updated_at": d.updated_at, "url": d.url}
                             for d in vis if d.source in ("confluence", "gdrive")][:5],
            "suggested_questions": suggestions.get(p.email, []),
            "alerts": runtime.brain.alerts(p),
        }

    @app.get("/v1/explain-access")
    def explain_access(doc_id: str, p: Current):
        return runtime.brain.explain_access(p, doc_id)

    @app.get("/v1/alerts")
    def alerts(p: Current):
        return {"alerts": runtime.brain.alerts(p)}

    # -- audit (compliance) ------------------------------------------------------------------
    @app.post("/v1/audit/query")
    def audit_query(body: dict, p: Current):
        require(p, {COMPLIANCE_ROLE})
        return runtime.audit.query(p, body)

    @app.get("/v1/audit/verify")
    def audit_verify(p: Current):
        require(p, {COMPLIANCE_ROLE})
        return runtime.audit.verify()

    @app.get("/v1/audit/replay")
    def audit_replay(request_id: str, p: Current):
        require(p, {COMPLIANCE_ROLE})
        out = runtime.audit.replay(p, request_id)
        if out is None:
            raise HTTPException(404, "no such request")
        return out

    @app.get("/v1/audit/time-travel")
    def time_travel(user: str, at: str, p: Current):
        require(p, {COMPLIANCE_ROLE})
        raise HTTPException(501, "time-travel queries arrive with the bi-temporal ACL snapshots (slice 3)")

    # -- admin and ops --------------------------------------------------------------------------
    @app.get("/v1/freshness")
    def freshness(p: Current):
        require(p, ADMIN_ROLES)
        from connectors.ingestion.freshness_report import report
        store = runtime.brain.store
        if store is None:
            return {"as_of": _now(), "window_hours": 24, "sources": {}}
        return report(store)

    @app.get("/v1/leakci/latest")
    def leakci(p: Current):
        """The scoreboard Leak-CI wrote last (`python -m evals.leakci`, Workstream C), or an empty one."""
        require(p, ADMIN_ROLES)
        path = Path(settings.leakci_scoreboard)
        if not path.is_absolute():
            path = REPO_ROOT / path
        try:
            board = json.loads(path.read_text(encoding="utf8"))
        except (OSError, ValueError):
            return {"as_of": _now(), "cases": 0, "leaks": 0, "suites": [], "note": "no Leak-CI run recorded yet"}
        return {k: board.get(k) for k in ("as_of", "cases", "leaks", "suites", "target")}

    @app.get("/v1/policy/versions")
    def policy_versions(p: Current):
        require(p, ADMIN_ROLES)
        return {"versions": [{"policy_version": POLICY_VERSION, "author": "ws-b", "created_at": "2026-10-08T00:00:00Z",
                              "pr_url": None}], "active": POLICY_VERSION}

    @app.post("/v1/policy/evaluate")
    def policy_evaluate(body: EvaluateBody, p: Current):
        require(p, ADMIN_ROLES)
        out = runtime.brain.evaluate(body.user, body.doc_id)
        log_admin(p, "policy.evaluate", {"user": body.user, "doc_id": body.doc_id if out["allowed"] else None,
                                         "allowed": out["allowed"]})
        return out

    # -- development helpers (fixture runtime, demo of tampering) ------------------------------
    if settings.dev_auth:
        @app.post("/sim/tamper")
        def sim_tamper(seq: int):
            if runtime.tamper is None:
                raise HTTPException(404, "not available")
            runtime.tamper(seq)
            return {"tampered": seq}

        @app.post("/sim/advance")
        def sim_advance(body: AdvanceBody):
            if runtime.advance is None:
                raise HTTPException(404, "scripted events need the fixture runtime; use the simulator admin API")
            runtime.advance(body.event_id, body.ingest)
            return {"applied": body.event_id}

        @app.post("/sim/reset")
        def sim_reset():
            if runtime.reset is None:
                raise HTTPException(404, "not available")
            runtime.reset()
            return {"ok": True}

    # Last, so every route above wins; the MCP app answers /mcp and its /.well-known metadata only.
    if mcp_app is not None:
        app.mount("/", mcp_app)

    return app

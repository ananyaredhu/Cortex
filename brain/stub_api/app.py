"""Stub of the Brain HTTP API (docs/02-contracts/api.md, v0.2) backed by the shared fixtures.

Purpose: let Workstream C build the UI and the golden-test harness before Workstream B's real pipeline
exists, and give B a reference for response shapes. It is NOT the real system: retrieval is keyword
overlap, the "LLM" is a template, and permissions are the token-overlap rule only.

Auth (stub only): `Authorization: Bearer dev:<persona_id>`, e.g. `Bearer dev:priya`.
The real API validates a JWT from the mock IdP on every request.

The 0.2 fields are filled with plausible stub values: grounding is always 1.0, `time_range` is accepted and ignored,
`clarify` is always null. Sources are fresh and reachable unless `/sim/source-status` says otherwise.

Run: uvicorn brain.stub_api.app:app --reload --port 8000
"""
import hashlib
import json
import re
from collections.abc import Iterator

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from fixtures.loader import State, can_see

app = FastAPI(title="Internal Brain stub API", version="0.1")
STATE = State()
AUDIT: list[dict] = []
ANSWERED: dict[str, list[dict]] = {}   # persona -> [{request_id, doc_ids}]
CONVERSATIONS: dict[str, dict[str, dict]] = {}   # persona -> conversation_id -> {title, last_asked_at}
SOURCE_STATUS: dict[str, str] = {}   # source -> "stale" | "unavailable" (absent: "ok"), set by /sim/source-status
SALT = "stub-salt"
REFUSAL = "I couldn't find anything you have access to about that."
SOURCES = ("confluence", "jira", "slack", "gdrive")
POLICY_VERSION = "stub-0.1"
STAGES = ("retrieve", "authorize", "verify_live", "generate", "check")
ADMIN_ROLES = {"security-lead", "compliance"}
STOP = {"the", "and", "what", "whats", "were", "was", "there", "that", "this", "with", "from", "show", "have",
        "about", "which", "for", "are", "any", "our", "you", "did", "does", "can", "how", "who", "when", "into"}
INJECTION = re.compile(r"[^.]*ignore (all )?(previous|prior) instructions[^.]*\.?", re.I)


# ---------------------------------------------------------------------------------------------------
def _persona(authorization: str | None) -> dict:
    if not authorization or not authorization.startswith("Bearer dev:"):
        raise HTTPException(401, "Use 'Authorization: Bearer dev:<persona_id>' with the stub API")
    pid = authorization.removeprefix("Bearer dev:")
    p = next((x for x in STATE.data["personas"] if x["id"] == pid), None)
    if p is None:
        raise HTTPException(401, "unknown persona")
    return p


def _require(me: dict, roles: set[str]) -> None:
    if not roles & set(me["roles"]):
        raise HTTPException(403, f"one of these roles is required: {', '.join(sorted(roles))}")


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) >= 3 and t not in STOP}


def _score(question: str, doc: dict) -> int:
    return len(_tokens(question) & _tokens(doc["title"] + " " + doc["body"]))


def _sanitize(body: str) -> tuple[str, list[str]]:
    if INJECTION.search(body):
        return INJECTION.sub("[removed: possible prompt injection]", body), ["possible_injection"]
    return body, []


def _hash(obj) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _append_audit(event: dict) -> None:
    event["seq"] = len(AUDIT) + 1
    event["ts"] = STATE.data["now"]
    event["prev_hash"] = AUDIT[-1]["hash"] if AUDIT else "genesis"
    event["hash"] = _hash({k: v for k, v in event.items() if k != "hash"})
    AUDIT.append(event)


class AskBody(BaseModel):
    question: str
    conversation_id: str | None = None
    skill_hint: str | None = None
    sources: list[str] | None = None
    time_range: str | None = None


class EvaluateBody(BaseModel):
    user: str
    doc_id: str


class SourceStatusBody(BaseModel):
    source: str
    status: str   # ok | stale | unavailable


class AdvanceBody(BaseModel):
    event_id: str


# ---------------------------------------------------------------------------------------------------
@app.get("/v1/health")
def health():
    return {"ok": True, "stub": True}


@app.post("/v1/ask")
def ask(body: AskBody, authorization: str | None = Header(None)):
    return _answer(_persona(authorization), body)


@app.post("/v1/ask/stream")
def ask_stream(body: AskBody, authorization: str | None = Header(None)):
    me = _persona(authorization)

    def events() -> Iterator[str]:
        for stage in STAGES:   # always all five, refusals included
            yield _sse("stage", {"stage": stage, "status": "start"})
            if stage == "generate":
                result = _answer(me, body)
            yield _sse("stage", {"stage": stage, "status": "done"})
        yield _sse("result", result)

    return StreamingResponse(events(), media_type="text/event-stream")


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _answer(me: dict, body: AskBody) -> dict:
    toks = STATE.persona_tokens[me["id"]]
    wanted = [s for s in SOURCES if not body.sources or s in body.sources]
    unavailable = [s for s in wanted if SOURCE_STATUS.get(s) == "unavailable"]
    searched = [s for s in wanted if s not in unavailable]
    scored = sorted(((_score(body.question, d), d) for d in STATE.docs.values() if d["source"] in searched),
                    key=lambda x: -x[0])
    candidates = [(s, d) for s, d in scored if s >= 2][:8]
    allowed = [d for s, d in candidates if can_see(toks, d)][:5]
    denied = [d for s, d in candidates if not can_see(toks, d)]
    request_id = f"req_{len(AUDIT) + 1:04d}"

    claims, citations, flags, lines = [], [], [], []
    for d in allowed:
        text, f = _sanitize(d["body"])
        flags += f
        snippet = text[:200]
        lines.append(f"- {d['title']}: {snippet}")
        claims.append({"text": f"{d['title']}: {snippet}", "citations": [d["doc_id"]]})
        citations.append({"doc_id": d["doc_id"], "title": d["title"], "url": d["url"], "source": d["source"],
                          "as_of": d["updated_at"],
                          "why_visible": [f"user:{me['email']}", sorted(toks & set(d["acl"]["tokens"]))[0]],
                          "excerpt": text[:280]})

    refused = not allowed
    answer = REFUSAL if refused else "Here is what I found:\n" + "\n".join(lines)
    ANSWERED.setdefault(me["id"], []).append({"request_id": request_id, "doc_ids": [d["doc_id"] for d in allowed]})

    decisions = [{"doc_id": d["doc_id"], "allowed": True, "proof_path": c["why_visible"],
                  "acl_snapshot_hash": d["acl"]["snapshot_hash"], "policy_version": "stub-0.1",
                  "doc_version": d["version"], "jit_checked": True} for d, c in zip(allowed, citations, strict=True)]
    decisions += [{"doc_id_hash": _hash([SALT, d["doc_id"]]), "allowed": False, "reason": "no_access",
                   "acl_snapshot_hash": d["acl"]["snapshot_hash"], "policy_version": "stub-0.1"} for d in denied]
    _append_audit({"request_id": request_id, "event_type": "ask",
                   "actor": {"user_id": me["email"], "roles": me["roles"], "client": "ui"},
                   "query": {"text": body.question, "skill": body.skill_hint},
                   "decisions": decisions,
                   "answer": {"text": answer, "sha256": _hash(answer), "citations": [c["doc_id"] for c in citations],
                              "refused": refused},
                   "flags": sorted(set(flags))})
    conversation_id = body.conversation_id or f"c_{sum(len(c) for c in CONVERSATIONS.values()) + 1}"
    conv = CONVERSATIONS.setdefault(me["id"], {}).setdefault(conversation_id, {"title": body.question})
    conv["last_asked_at"] = STATE.data["now"]
    return {"request_id": request_id, "conversation_id": conversation_id, "answer": answer,
            "claims": claims, "citations": citations, "refused": refused, "abstained": False,
            "freshness": {"oldest_source_as_of": min((c["as_of"] for c in citations), default=None),
                          "stale_refetched": 0,
                          "per_source": {s: {"last_sync": STATE.data["now"], "status": SOURCE_STATUS.get(s, "ok")}
                                         for s in SOURCES}},
            "skill": body.skill_hint,
            # counts only what the asker is shown: never candidates or denials (api.md 0.2, coverage)
            "coverage": {s: {"searched": s in searched, "shown": sum(c["source"] == s for c in citations)}
                         for s in SOURCES},
            "grounding": None if refused else {"score": 1.0, "removed_claims": 0},
            "policy_version": POLICY_VERSION, "unavailable_sources": unavailable, "clarify": None}


@app.get("/v1/conversations")
def conversations(authorization: str | None = Header(None)):
    me = _persona(authorization)
    mine = CONVERSATIONS.get(me["id"], {})
    return {"conversations": [{"conversation_id": cid, **c} for cid, c in reversed(mine.items())]}


@app.get("/v1/mywork")
def mywork(authorization: str | None = Header(None)):
    me = _persona(authorization)
    vis = STATE.visible_docs(me["id"])
    return {
        "user": {"display_name": me["display_name"], "roles": me["roles"]},
        "issues": [{"doc_id": d["doc_id"], "title": d["title"], "url": d["url"], "status": _status(d["body"])}
                   for d in vis if d["source"] == "jira"],
        "projects": sorted({d["parent_id"] for d in vis if d["source"] == "jira"}),
        "channels": sorted({d["parent_id"] for d in vis if d["source"] == "slack"}),
        "recent_pages": [{"doc_id": d["doc_id"], "title": d["title"], "updated_at": d["updated_at"], "url": d["url"]}
                         for d in sorted((x for x in vis if x["source"] in ("confluence", "gdrive")),
                                         key=lambda x: x["updated_at"], reverse=True)[:5]],
        "suggested_questions": [g["question"] for g in STATE.data["golden"] if g["persona"] == me["id"]][:3],
        "alerts": alerts(authorization)["alerts"],
    }


def _status(body: str) -> str | None:
    m = re.search(r"Status: ([A-Za-z ]+)\.", body)
    return m.group(1) if m else None


@app.get("/v1/explain-access")
def explain_access(doc_id: str, authorization: str | None = Header(None)):
    me = _persona(authorization)
    d = STATE.docs.get(doc_id)
    toks = STATE.persona_tokens[me["id"]]
    if d is None or not can_see(toks, d):
        return {"found": False}   # identical for forbidden and nonexistent
    return {"found": True, "proof_path": [f"user:{me['email']}", sorted(toks & set(d["acl"]["tokens"]))[0]]}


@app.get("/v1/alerts")
def alerts(authorization: str | None = Header(None)):
    me = _persona(authorization)
    out = []
    for ev_id in STATE.applied:
        ev = next(e for e in STATE.data["events"] if e["id"] == ev_id)
        if ev["type"] != "upsert":
            continue
        for ans in ANSWERED.get(me["id"], []):
            if ev["doc_id"] in ans["doc_ids"]:
                out.append({"request_id": ans["request_id"], "changed_doc": ev["doc_id"],
                            "changed_at": ev["at"], "summary": ev.get("note", "A source changed after your answer")})
    return {"alerts": out}


@app.post("/v1/audit/query")
def audit_query(body: dict, authorization: str | None = Header(None)):
    """Filters: `user` (email), `space` (e.g. "confluence:PAY", matched on allowed document ids only: denied ones
    are salted hashes by design), `decision` ("allowed" or "denied": events with at least one such decision).
    `question` (natural language) is accepted and ignored by the stub. The query is itself logged."""
    me = _persona(authorization)
    if "compliance" not in me["roles"]:
        raise HTTPException(403, "compliance role required")
    f = body.get("filter") or {}
    user, space, decision = f.get("user"), f.get("space"), f.get("decision")
    events = [e for e in AUDIT
              if (user is None or e["actor"]["user_id"] == user)
              and (space is None or any(d.get("doc_id", "").startswith(space + "/") for d in e["decisions"]))
              and (decision in (None, "all") or any(d["allowed"] is (decision == "allowed") for d in e["decisions"]))]
    shown = [_for_officer(e, STATE.persona_tokens[me["id"]]) for e in events]
    _append_audit({"request_id": f"aq_{len(AUDIT) + 1:04d}", "event_type": "audit_query",
                   "actor": {"user_id": me["email"], "roles": me["roles"], "client": "ui"},
                   "query": {"text": body.get("question") or json.dumps(f, sort_keys=True), "skill": None},
                   "decisions": [], "flags": []})
    return {"events": shown, "count": len(shown)}


def _for_officer(event: dict, officer_tokens: set[str]) -> dict:
    """An answer's text goes to the officer only if they may see every document it cites; the hash always does."""
    answer = event.get("answer")
    if not answer:
        return event
    cited = [STATE.docs.get(c) for c in answer["citations"]]
    if all(d is not None and can_see(officer_tokens, d) for d in cited):
        return event
    return {**event, "answer": {**{k: v for k, v in answer.items() if k != "text"}, "text": None, "text_withheld": True}}


@app.get("/v1/audit/verify")
def audit_verify(authorization: str | None = Header(None)):
    _require(_persona(authorization), {"compliance"})
    prev = "genesis"
    for e in AUDIT:
        body = {k: v for k, v in e.items() if k != "hash"}
        if e["prev_hash"] != prev or e["hash"] != _hash(body):
            return {"ok": False, "first_broken_seq": e["seq"], "reason": "hash mismatch"}
        prev = e["hash"]
    return {"ok": True, "checked": len(AUDIT), "checkpoints": 0}


@app.get("/v1/audit/replay")
def audit_replay(request_id: str, authorization: str | None = Header(None)):
    viewer = _persona(authorization)
    _require(viewer, {"compliance"})
    ev = next((e for e in AUDIT if e.get("request_id") == request_id), None)
    if ev is None:
        raise HTTPException(404, "unknown request_id")
    asker = next(p for p in STATE.data["personas"] if p["email"] == ev["actor"]["user_id"])
    viewer_toks, asker_toks = STATE.persona_tokens[viewer["id"]], STATE.persona_tokens[asker["id"]]

    def shown(doc_id: str) -> dict:   # titles only where the viewing officer may see the document
        d = STATE.docs.get(doc_id)
        if d is None or not can_see(viewer_toks, d):
            return {"doc_id": doc_id, "restricted": True}
        return {"doc_id": doc_id, "title": d["title"], "source": d["source"]}

    then = ev["answer"]["citations"]
    now = [c for c in then if c in STATE.docs and can_see(asker_toks, STATE.docs[c])]
    logged = {d["doc_id"]: d["doc_version"] for d in ev["decisions"] if d["allowed"]}
    diffs = [{"doc_id": c, "change": "revoked"} for c in then if c not in now]
    diffs += [{"doc_id": c, "change": "edited"} for c in now if STATE.docs[c]["version"] != logged.get(c)]
    return {"then": {"answer_sha256": ev["answer"]["sha256"], "citations": [shown(c) for c in then],
                     "policy_version": ev["decisions"][0]["policy_version"] if ev["decisions"] else POLICY_VERSION},
            "now": {"citations": [shown(c) for c in now], "policy_version": POLICY_VERSION},
            "differences": diffs}


@app.get("/v1/freshness")
def freshness(authorization: str | None = Header(None)):
    _require(_persona(authorization), ADMIN_ROLES)
    lag = {"count": 12, "p50": 20.0, "p95": 90.0, "max": 110.0}   # stub numbers; the real ones come from A's report
    return {"as_of": STATE.data["now"], "window_hours": 24.0,
            "sources": {s: {"last_run_at": STATE.data["now"], "last_ok_at": STATE.data["now"], "last_error": None,
                            "freshness_lag_seconds": lag, "pipeline_lag_seconds": lag, "by_trigger": {}}
                        for s in SOURCES}}


@app.get("/v1/leakci/latest")
def leakci(authorization: str | None = Header(None)):
    _require(_persona(authorization), ADMIN_ROLES)
    suites = [{"name": n, "category": c, "passed": 1, "failed": 0, "last_run_at": STATE.data["now"]}
              for n, c in (("Prompt injection in a document", "injection"), ("Existence side-channel", "side-channel"),
                           ("Revocation on next query", "revocation"), ("Canary strings", "leak"))]
    return {"as_of": STATE.data["now"], "cases": len(STATE.data["golden"]), "leaks": 0, "suites": suites, "stub": True}


@app.get("/v1/policy/versions")
def policy_versions(authorization: str | None = Header(None)):
    _require(_persona(authorization), ADMIN_ROLES)
    return {"active": POLICY_VERSION,
            "versions": [{"policy_version": POLICY_VERSION, "author": "stub", "created_at": STATE.data["now"], "pr_url": None}]}


@app.post("/v1/policy/evaluate")
def policy_evaluate(body: EvaluateBody, authorization: str | None = Header(None)):
    _require(_persona(authorization), ADMIN_ROLES)
    who = next((p for p in STATE.data["personas"] if p["email"] == body.user), None)
    d = STATE.docs.get(body.doc_id)
    if who is None or d is None:
        raise HTTPException(404, "unknown user or document")
    shared = sorted(STATE.persona_tokens[who["id"]] & set(d["acl"]["tokens"]))
    admin = _persona(authorization)
    _append_audit({"request_id": f"ev_{len(AUDIT) + 1:04d}", "event_type": "admin_view",
                   "actor": {"user_id": admin["email"], "roles": admin["roles"], "client": "ui"},
                   "query": {"text": f"policy evaluate {body.user} {body.doc_id}", "skill": None},
                   "decisions": [], "flags": []})
    return {"allowed": bool(shared), "rule": "token-overlap", "policy_version": POLICY_VERSION,
            "proof_path": [f"user:{who['email']}", shared[0]] if shared else []}


# --- stub-only simulation helpers (the real equivalents are simulator admin endpoints, owned by A) -------------
@app.post("/sim/advance")
def sim_advance(body: AdvanceBody):
    STATE.advance(body.event_id)
    return {"applied": STATE.applied}


@app.post("/sim/reset")
def sim_reset():
    global STATE
    STATE = State()
    AUDIT.clear()
    ANSWERED.clear()
    CONVERSATIONS.clear()
    SOURCE_STATUS.clear()
    return {"ok": True}


@app.post("/sim/source-status")
def sim_source_status(body: SourceStatusBody):
    """Demo the stale and unreachable banners: mark a source stale, unavailable, or back to ok."""
    if body.source not in SOURCES or body.status not in ("ok", "stale", "unavailable"):
        raise HTTPException(422, "unknown source or status")
    if body.status == "ok":
        SOURCE_STATUS.pop(body.source, None)
    else:
        SOURCE_STATUS[body.source] = body.status
    return {"source_status": SOURCE_STATUS}


@app.post("/sim/tamper")
def sim_tamper(seq: int):
    AUDIT[seq - 1]["query"]["text"] = "TAMPERED"
    return {"tampered": seq}

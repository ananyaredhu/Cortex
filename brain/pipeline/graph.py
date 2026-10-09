"""The query path (01-architecture.md, "Query path"), one LangGraph node per step.

identity -> retrieve -> authorize -> freshness -> generate -> check -> finalize -> audit

Policy nodes (identity, authorize, freshness, the denied-set scan inside check) are deterministic code over the
connectors and the index. The generator sees only the context packet. Every request runs every node, refusals
included, and takes at least `floor_latency_ms`, so a forbidden document and a nonexistent one look and time alike.
"""
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from brain import packet as packetlib
from brain.audit.chain import sha256_text
from brain.audit.store import AuditStore
from brain.auth import Principal
from brain.checker import CheckResult, GroundingScorer, Layer2Result, check, check_layer2
from brain.checker.layer2 import rebuild_answer
from brain.config import POLICY_VERSION, REFUSAL, SOURCES, STAGES, Settings
from brain.gateway.models import Generated, Generator
from brain.policy.identity import AskerIdentity, IdentityResolver
from brain.policy.labels import acl_label
from brain.policy.leakscan import DeniedDoc
from brain.policy.pdp import PDP, Decision, hash_denied
from brain.retrieval import Candidate, IndexReader, hybrid_search, query_terms
from connectors.base import Connector, DocumentNotFound
from connectors.ingestion.embedding import Embedder
from connectors.ingestion.store import Store

StageCallback = Callable[[str, str], None]        # (stage, "start" | "done")
NODE_STAGE = {"identity": "retrieve", "retrieve": "retrieve", "authorize": "authorize", "freshness": "verify_live",
              "generate": "generate", "check": "check", "finalize": "check", "audit": "check"}
STALE_AFTER_S = 300          # a source whose last successful pass is older than this is reported "stale"


@dataclass
class AskRequest:
    question: str
    conversation_id: str | None = None
    skill_hint: str | None = None
    sources: list[str] | None = None
    time_range: str | None = None


@dataclass
class Answered:
    """What we remember about an answer: enough for alerts, replay and conversations, labeled by its sources."""
    request_id: str
    email: str
    question: str
    conversation_id: str
    asked_at: str
    doc_versions: dict[str, str | None]
    label: list[list[str]] = field(default_factory=list)


class State(TypedDict, total=False):
    request_id: str
    principal: Principal
    request: AskRequest
    started: float
    asker: AskerIdentity
    searched: list[str]
    unavailable: list[str]
    terms: list[str]
    candidates: list[Candidate]
    decisions: list[Decision]
    allowed: list[Candidate]
    denied_docs: list[DeniedDoc]
    evidence: list[packetlib.Evidence]
    stale_refetched: int
    packet: dict
    generated: Generated
    checked: CheckResult
    grounded: Layer2Result
    flags: list[str]
    response: dict
    on_stage: Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Brain:
    def __init__(self, settings: Settings, connectors: Mapping[str, Connector], index: IndexReader, store: Store | None,
                 embedder: Embedder | None, generator: Generator, audit: AuditStore, *,
                 resolver: IdentityResolver | None = None, pdp: PDP | None = None,
                 scorer: GroundingScorer | None = None) -> None:
        self.settings = settings
        self.connectors = dict(connectors)
        self.index = index
        self.store = store
        self.embedder = embedder
        self.generator = generator
        self.audit = audit
        self.scorer = scorer
        self.resolver = resolver or IdentityResolver(self.connectors, ttl_s=settings.identity_ttl_s)
        self.pdp = pdp or PDP(self.connectors, policy_version=POLICY_VERSION, ttl_s=settings.decision_ttl_s)
        self.answers: dict[str, Answered] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="brain")
        self._graph = self._build()

    # -- graph ----------------------------------------------------------------------------------
    def _build(self):
        g = StateGraph(State)
        nodes = [("identity", self._identity), ("retrieve", self._retrieve), ("authorize", self._authorize),
                 ("freshness", self._freshness), ("generate", self._generate), ("check", self._check),
                 ("finalize", self._finalize), ("audit", self._audit)]
        for name, fn in nodes:
            g.add_node(name, self._staged(name, fn))
        g.add_edge(START, nodes[0][0])
        for (a, _), (b, _) in zip(nodes, nodes[1:], strict=False):
            g.add_edge(a, b)
        g.add_edge(nodes[-1][0], END)
        return g.compile()

    @staticmethod
    def _staged(name: str, fn):
        stage = NODE_STAGE[name]
        first = [n for n, s in NODE_STAGE.items() if s == stage][0] == name
        last = [n for n, s in NODE_STAGE.items() if s == stage][-1] == name

        def run(state: State) -> dict:
            cb = state.get("on_stage")
            if cb and first:
                cb(stage, "start")
            out = fn(state)
            if cb and last:
                cb(stage, "done")
            return out
        return run

    def ask(self, principal: Principal, request: AskRequest, on_stage: StageCallback | None = None) -> dict:
        started = time.monotonic()
        state: State = {"request_id": f"req_{uuid.uuid4().hex[:8]}", "principal": principal, "request": request,
                        "started": started, "on_stage": on_stage, "flags": []}
        final = self._graph.invoke(state)
        remaining = self.settings.floor_latency_ms / 1000 - (time.monotonic() - started)
        if remaining > 0:
            time.sleep(remaining)
        return final["response"]

    # -- nodes ----------------------------------------------------------------------------------
    def _identity(self, state: State) -> dict:
        return {"asker": self.resolver.resolve(state["principal"].email)}

    def _retrieve(self, state: State) -> dict:
        req, asker = state["request"], state["asker"]
        wanted = [s for s in self.settings.sources if not req.sources or s in req.sources]
        unavailable = [s for s in wanted if s in asker.unavailable]
        searched = [s for s in wanted if s in asker.identities and s not in unavailable]
        terms = query_terms(req.question)
        vector = model = None
        if self.embedder is not None and self.embedder.model not in ("none",):
            try:
                vector = self.embedder.embed([req.question])[0]
                model = self.embedder.model
            except Exception:                        # noqa: BLE001 - the keyword leg still runs
                vector = None
        candidates = hybrid_search(self.index, req.question, asker.tokens, searched, vector=vector, model=model,
                                   candidates=self.settings.candidates, max_distance=self.settings.max_vector_distance)
        return {"searched": searched, "unavailable": unavailable, "terms": terms, "candidates": candidates}

    def _authorize(self, state: State) -> dict:
        asker = state["asker"]
        versions = {c.doc_id: self._indexed_version(c.doc_id) for c in state["candidates"]}
        decisions = self.pdp.check_many(asker, [(c.doc_id, versions[c.doc_id]) for c in state["candidates"]])
        allowed_ids = {d.doc_id for d in decisions if d.allowed}
        allowed = [c for c in state["candidates"] if c.doc_id in allowed_ids]
        denied_docs = []
        for c in state["candidates"]:
            if c.doc_id in allowed_ids:
                continue
            row = self.index.document(c.doc_id)
            text = " ".join(ch.text for ch in self.index.chunks_of(c.doc_id))
            denied_docs.append(DeniedDoc(c.doc_id, row.title if row else "", text))
        return {"decisions": decisions, "allowed": allowed, "denied_docs": denied_docs}

    def _indexed_version(self, doc_id: str) -> str | None:
        chunks = self.index.chunks_of(doc_id)
        return chunks[0].source_version if chunks else None

    def _freshness(self, state: State) -> dict:
        """Read-through: compare the indexed version with the source's; re-fetch inline when they differ."""
        terms = state["terms"]
        stale = 0
        evidence: list[packetlib.Evidence] = []

        def one(cand: Candidate):
            row = self.index.document(cand.doc_id)
            chunks = self.index.chunks_of(cand.doc_id)
            if row is None or not chunks:
                return None, 0
            connector = self.connectors.get(cand.source)
            text_by_chunk = {c.chunk_id: c.text for c in chunks}
            title, url, as_of, tokens = row.title, row.url, row.updated_at, chunks[0].acl_tokens
            refetched = 0
            try:
                if connector is not None and connector.version(cand.doc_id) != chunks[0].source_version:
                    doc = connector.fetch(cand.doc_id)
                    text_by_chunk = {f"{cand.doc_id}#0": doc.body}
                    title, url, as_of, tokens = doc.title, doc.url, doc.updated_at, list(doc.acl.tokens)
                    cand.chunk_ids = [f"{cand.doc_id}#0"]
                    refetched = 1
            except DocumentNotFound:
                return None, 0
            except Exception:                        # noqa: BLE001 - keep the indexed copy, which the PDP allowed
                pass
            best = [cid for cid in cand.chunk_ids if cid in text_by_chunk] or [chunks[0].chunk_id]
            text = " ".join(text_by_chunk[cid] for cid in best[:2])
            ev = packetlib.make_evidence(best[0], cand.doc_id, cand.source, title, url, text, as_of, tokens, terms, cand.score)
            return ev, refetched

        for ev, refetched in self._pool.map(one, state["allowed"]):
            stale += refetched
            if ev is not None:
                evidence.append(ev)
        return {"evidence": evidence, "stale_refetched": stale}

    def _generate(self, state: State) -> dict:
        p, asker = state["principal"], state["asker"]
        user_context = {"display_name": p.display_name or p.email, "sources": list(asker.sources())}
        pkt = packetlib.build_packet(state["request_id"], user_context, state["request"].question,
                                     state["request"].skill_hint, state["evidence"],
                                     per_source_quota=self.settings.per_source_quota, max_evidence=self.settings.max_evidence)
        flags = sorted({f for e in pkt["evidence"] for f in e["flags"]})
        return {"packet": pkt, "generated": self.generator.generate(pkt), "flags": flags}

    def _check(self, state: State) -> dict:
        allowed_ids = {e["doc_id"] for e in state["packet"]["evidence"]}
        checked = check(state["generated"], allowed_ids, state["denied_docs"])
        evidence_text = {e.doc_id: f"{e.title}. {e.raw}" for e in state["evidence"]}
        grounded = check_layer2(checked.claims, evidence_text, self.scorer, threshold=self.settings.grounding_threshold)
        if grounded.removed_claims:
            checked = CheckResult(rebuild_answer(checked.answer, grounded.claims, grounded.removed_claims), grounded.claims,
                                  checked.removed_claims + grounded.removed_claims, checked.leak_hits,
                                  abstained=checked.abstained or not grounded.claims)
        return {"checked": checked, "grounded": grounded}

    def _finalize(self, state: State) -> dict:
        req, checked, pkt = state["request"], state["checked"], state["packet"]
        proof = {d.doc_id: list(d.proof_path) for d in state["decisions"] if d.allowed}
        refused = not pkt["evidence"]
        abstained = (not refused) and checked.abstained
        shown_ids = {d for c in checked.claims for d in c["citations"]} if not (refused or abstained) else set()
        by_id = {e["doc_id"]: e for e in pkt["evidence"]}
        excerpts = {e.doc_id: e.raw for e in state["evidence"]}
        citations = [{"doc_id": d, "title": by_id[d]["title"], "url": by_id[d]["url"], "source": by_id[d]["source"],
                      "as_of": by_id[d]["as_of"], "why_visible": proof.get(d, []), "excerpt": excerpts.get(d, "")[:280]}
                     for d in by_id if d in shown_ids]
        if refused:
            answer, claims = REFUSAL, []
        elif abstained:
            answer, claims = "I found sources you can see, but none of them supports an answer to that.", []
        else:
            answer, claims = checked.answer, checked.claims
        conversation_id = req.conversation_id or f"c_{uuid.uuid4().hex[:6]}"
        response = {
            "request_id": state["request_id"], "conversation_id": conversation_id, "answer": answer, "claims": claims,
            "citations": citations, "refused": refused, "abstained": abstained,
            "freshness": {"oldest_source_as_of": min((c["as_of"] for c in citations if c["as_of"]), default=None),
                          "stale_refetched": state["stale_refetched"], "per_source": self._per_source(state["unavailable"])},
            "skill": req.skill_hint,
            "coverage": {s: {"searched": s in state["searched"], "shown": sum(c["source"] == s for c in citations)}
                         for s in SOURCES},
            "grounding": None if (refused or abstained) else checked.grounding,
            "policy_version": self.pdp.policy_version, "unavailable_sources": state["unavailable"], "clarify": None,
        }
        return {"response": response}

    def _per_source(self, unavailable: list[str]) -> dict:
        runs = {r.source: r for r in (self.store.runs() if self.store is not None else [])}
        now = datetime.now(UTC)
        out = {}
        for s in SOURCES:
            run = runs.get(s)
            last = run.last_ok_at if run else None
            status = "ok"
            if s in unavailable:
                status = "unavailable"
            elif last:
                try:
                    age = (now - datetime.fromisoformat(last.replace("Z", "+00:00"))).total_seconds()
                    status = "stale" if age > STALE_AFTER_S else "ok"
                except ValueError:
                    status = "ok"
            out[s] = {"last_sync": last, "status": status}
        return out

    def _audit(self, state: State) -> dict:
        p, req, resp, checked = state["principal"], state["request"], state["response"], state["checked"]
        salt = self.settings.denied_id_salt
        cited = [c["doc_id"] for c in resp["citations"]]
        label = acl_label(e["acl_label"] for e in state["packet"]["evidence"] if e["doc_id"] in cited)
        event = {
            "request_id": state["request_id"], "event_type": "ask",
            "actor": {"user_id": p.email, "roles": list(p.roles), "client": p.client},
            "query": {"text": req.question, "skill": req.skill_hint},
            "decisions": [d.audit_view(salt) for d in state["decisions"]],
            "answer": {"text": resp["answer"], "sha256": sha256_text(resp["answer"]), "citations": cited,
                       "refused": resp["refused"], "abstained": resp["abstained"], "acl_label": label},
            "checks": {**checked.status, "grounding_model": state["grounded"].status,
                       "grounding_scores": state["grounded"].scores,
                       "leak_hits": [hash_denied(d, salt) for d in checked.leak_hits]},
            "models": {"generator": self.generator.model, "checker": state["grounded"].model,
                       "embedding": f"{self.embedder.model}@{self.embedder.version}" if self.embedder else "none"},
            "flags": state["flags"],
            "latency_ms": int((time.monotonic() - state["started"]) * 1000),
        }
        self.audit.append(event)
        versions = {d.doc_id: d.doc_version for d in state["decisions"] if d.allowed and d.doc_id in cited}
        with self._lock:
            self.answers[state["request_id"]] = Answered(state["request_id"], p.email, req.question,
                                                         resp["conversation_id"], utc_now(), versions, label)
        return {}

    # -- other front-door operations, all through the PDP ---------------------------------------
    def explain_access(self, principal: Principal, doc_id: str) -> dict:
        asker = self.resolver.resolve(principal.email)
        decision = self.pdp.check(asker, doc_id, self._indexed_version(doc_id))
        if not decision.allowed:
            return {"found": False}
        return {"found": True, "proof_path": list(decision.proof_path)}

    def evaluate(self, user_email: str, doc_id: str) -> dict:
        """Admin sandbox: the PDP's answer for someone else. Caller checks the role and logs `admin_view`."""
        asker = self.resolver.resolve(user_email)
        d = self.pdp.check(asker, doc_id, self._indexed_version(doc_id))
        rule = "live check_access allowed" if d.allowed else {"no_access": "live check_access denied",
                                                                "not_found": "no such document (or denied)",
                                                                "no_identity": "no identity on that platform",
                                                                "error": "connector error: fail closed"}.get(d.reason, d.reason)
        return {"allowed": d.allowed, "rule": rule, "proof_path": list(d.proof_path), "policy_version": d.policy_version}

    def visible_documents(self, principal: Principal, source: str | None = None, limit: int = 100) -> list:
        """Index rows the person may read right now: prefilter, then the live check on each."""
        asker = self.resolver.resolve(principal.email)
        rows = self.index.documents_visible(sorted(asker.tokens), source, limit)
        decisions = self.pdp.check_many(asker, [(r.doc_id, r.version) for r in rows])
        ok = {d.doc_id for d in decisions if d.allowed}
        return [r for r in rows if r.doc_id in ok]

    def alerts(self, principal: Principal) -> list[dict]:
        """Answers whose cited documents changed since, for documents the person may still see."""
        out = []
        asker = self.resolver.resolve(principal.email)
        with self._lock:
            mine = [a for a in self.answers.values() if a.email == principal.email]
        for a in mine:
            for doc_id, then in a.doc_versions.items():
                row = self.index.document(doc_id)
                now_version = self._indexed_version(doc_id)
                if row is None or now_version is None or now_version == then:
                    continue
                if not self.pdp.check(asker, doc_id, now_version).allowed:
                    continue
                out.append({"request_id": a.request_id, "question": a.question, "changed_doc": doc_id,
                            "changed_title": row.title, "changed_at": row.updated_at,
                            "summary": f"{row.title} changed after your answer"})
        return out

    def conversations(self, principal: Principal) -> list[dict]:
        with self._lock:
            mine = [a for a in self.answers.values() if a.email == principal.email]
        convs: dict[str, dict] = {}
        for a in sorted(mine, key=lambda a: a.asked_at):
            c = convs.setdefault(a.conversation_id, {"conversation_id": a.conversation_id, "title": a.question})
            c["last_asked_at"] = a.asked_at
        return sorted(convs.values(), key=lambda c: c["last_asked_at"], reverse=True)

    def stages(self) -> tuple[str, ...]:
        return STAGES

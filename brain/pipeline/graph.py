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
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from brain import packet as packetlib
from brain.audit.chain import sha256_text
from brain.audit.store import AuditStore
from brain.auth import Principal
from brain.checker import CheckResult, GroundingScorer, Layer2Result, check, check_layer2
from brain.checker.fields import FieldsResult, check_fields
from brain.checker.layer1 import render_answer
from brain.checker.layer2 import lead_in, rebuild_answer
from brain.config import ABSTAIN, POLICY_VERSION, REFUSAL, SOURCES, STAGES, UNAVAILABLE, Settings
from brain.gateway.models import Generated, Generator
from brain.gateway.titles import MAX_CUSTOM_TITLE, Titler, clean_title
from brain.pipeline.rewrite import rewrite_query
from brain.policy.identity import AskerIdentity, IdentityResolver
from brain.policy.labels import acl_label, may_serve
from brain.policy.leakscan import DeniedDoc, prepare
from brain.policy.pdp import PDP, Decision, hash_denied, source_of
from brain.policy.writes import is_assignee
from brain.projects import EXTRA_CANDIDATES, PROJECT_BOOST, ProjectView
from brain.projects import Registry as ProjectRegistry
from brain.projects.team import DONE, assignee_of, issue_view, status_of, team_focus
from brain.retrieval import Candidate, IndexReader, hybrid_search, query_terms
from brain.skills import Registry, Skill
from connectors.base import Connector, Document, DocumentNotFound, TransitionRefused
from connectors.ingestion.embedding import Embedder
from connectors.ingestion.store import Store

StageCallback = Callable[[str, str], None]        # (stage, "start" | "done")
NODE_STAGE = {"identity": "retrieve", "understand": "retrieve", "retrieve": "retrieve", "authorize": "authorize",
              "expand": "authorize",
              "freshness": "verify_live",
              "generate": "generate", "check": "check", "finalize": "check", "audit": "check"}
UNVERIFIED = "freshness_unverified"      # evidence whose live version check failed; the indexed copy was served
TEAM_DOCS = 500              # documents read per caller for Team focus; its counts cover at most this many
MY_ISSUES = 8                # the asker's own issues a "my work" skill adds to the candidates (skills-format, `adds`)
STALE_AFTER_S = 300          # a source whose last successful pass is older than this is reported "stale"


@dataclass
class AskRequest:
    question: str
    conversation_id: str | None = None
    skill_hint: str | None = None
    sources: list[str] | None = None
    time_range: str | None = None
    # Set only by brain.routines for a routine's run (never from a request body): the routine it belongs to, and the
    # name its new conversation gets (the routine's own name and the day, both the owner's words).
    routine_id: str | None = None
    title: str | None = None


class State(TypedDict, total=False):
    request_id: str
    principal: Principal
    request: AskRequest
    started: float
    asker: AskerIdentity
    history: list[dict]             # the person's earlier turns in this conversation, re-checked, oldest first
    search_query: str               # what retrieval looks for: the question, or a follow-up rewritten with the last turn
    rewritten: bool
    skill: Skill | None
    profile: list[ProjectView]      # the projects this person may see, with only the places they may open something in
    resolved: list[ProjectView]     # the ones the question names ("our migration")
    searched: list[str]
    unavailable: list[str]
    terms: list[str]
    candidates: list[Candidate]
    decisions: list[Decision]
    allowed: list[Candidate]
    denied_docs: list[DeniedDoc]
    linked: dict[str, str]          # link target doc_id -> the allowed document that links to it
    evidence: list[packetlib.Evidence]
    stale_refetched: int
    packet: dict
    generated: Generated
    checked: CheckResult
    by_field: FieldsResult
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
                 scorer: GroundingScorer | None = None, link_resolver: Callable[[str], str] | None = None,
                 skills: Registry | None = None, projects: ProjectRegistry | None = None,
                 container_resolver: Callable[[str], str] | None = None, token_resolver: Callable[[str], str] | None = None) -> None:
        # Brings the index in line with a document a connector just returned, through ingestion's own upsert path; set by
        # the runtime. None means the index waits for the next sync (move_issue then answers `indexed: false`).
        self.reindex: Callable[[Document], None] | None = None
        self.settings = settings
        self.connectors = dict(connectors)
        self.index = index
        self.store = store
        self.embedder = embedder
        self.generator = generator
        self.titler = Titler(generator)
        self.audit = audit
        self.scorer = scorer
        self.skills = skills if skills is not None else Registry.load()
        self.projects = projects if projects is not None else ProjectRegistry.load()
        self.container_resolver = container_resolver or (lambda x: x)       # registry container IDs -> the platforms' real ones
        self.token_resolver = token_resolver or (lambda x: x)
        self.link_resolver = link_resolver or (lambda doc_id: doc_id)   # stored link -> the ID the index and connectors use
        self.resolver = resolver or IdentityResolver(self.connectors, ttl_s=settings.identity_ttl_s)
        self.pdp = pdp or PDP(self.connectors, policy_version=POLICY_VERSION, ttl_s=settings.decision_ttl_s)
        self._lock = threading.Lock()
        self._identity_seen: dict[str, frozenset[str]] = {}
        self._leak_cache: dict[tuple[str, str], DeniedDoc] = {}      # prepared denied-set entries, by document and version
        for event in audit.all():                                 # the last snapshot per person survives a restart
            if event.get("event_type") == "identity_snapshot":
                self._identity_seen[event["identity"]["user"]] = frozenset(event["identity"]["tokens"])
        self.resolver.on_resolved = self._note_identity
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="brain")
        self._graph = self._build()

    def _note_identity(self, asker: AskerIdentity) -> None:
        """Write a person's token set to the audit log when it differs from the last one written (time-travel queries).

        Skipped while a connector is failing: that person's tokens from it are missing, not revoked."""
        if asker.unavailable:
            return
        with self._lock:
            if self._identity_seen.get(asker.email) == asker.tokens:
                return
            self._identity_seen[asker.email] = asker.tokens
        self.audit.append({"request_id": f"id_{uuid.uuid4().hex[:8]}", "event_type": "identity_snapshot",
                           "actor": {"user_id": "system:identity", "roles": [], "client": "brain"},
                           "identity": {"user": asker.email, "tokens": sorted(asker.tokens), "sources": list(asker.sources())},
                           "decisions": [], "flags": []})

    def baseline(self, emails) -> int:
        """Resolve each person afresh so their token set is on record (time-travel queries), whether or not they ever ask.

        A change since the last record is written; an unchanged set is not. Returns how many people were looked at."""
        looked = 0
        for email in sorted({e.strip().lower() for e in emails if e}):
            self.resolver.invalidate(email)
            self.resolver.resolve(email)
            looked += 1
        return looked

    # -- graph ----------------------------------------------------------------------------------
    def _build(self):
        g = StateGraph(State)
        nodes = [("identity", self._identity), ("understand", self._understand), ("retrieve", self._retrieve),
                 ("authorize", self._authorize), ("expand", self._expand), ("freshness", self._freshness),
                 ("generate", self._generate), ("check", self._check), ("finalize", self._finalize), ("audit", self._audit)]
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
        if request.conversation_id is None:               # a new conversation: name it, whatever the answer was
            self._name_later(principal, final["response"]["conversation_id"], request.question, request.title)
        remaining = self.settings.floor_latency_ms / 1000 - (time.monotonic() - started)
        if remaining > 0:
            time.sleep(remaining)
        return final["response"]

    # -- conversation titles (api.md 0.3): from the first question's text and nothing else ---------------------------
    def _name_later(self, principal: Principal, conversation_id: str, question: str, title: str | None = None) -> None:
        """Give a new conversation its generated title. Every conversation gets one in the same way, refused ones
        included, so a title says nothing about what exists. A model is asked on another thread, so `/ask` never waits
        for it; the rules need no waiting."""
        if title:                                          # a routine's run is named after the routine
            self._title_event(principal, conversation_id, clean_title(title, MAX_CUSTOM_TITLE), "generated", client="brain",
                              model="routine")
        elif self.titler.uses_model:
            self._pool.submit(self._name, principal, conversation_id, question)
        else:
            self._name(principal, conversation_id, question)

    def _name(self, principal: Principal, conversation_id: str, question: str) -> None:
        try:
            title, model = self.titler.title(question)
            self._title_event(principal, conversation_id, title, "generated", client="brain", model=model)
        except Exception:                                  # noqa: BLE001 - an unnamed chat keeps its question as its title
            pass

    def _title_event(self, principal: Principal, conversation_id: str, title: str | None, source: str, *, client: str,
                     model: str | None = None) -> None:
        event = {"request_id": f"title_{uuid.uuid4().hex[:8]}", "event_type": "conversation_title",
                 "conversation_id": conversation_id,
                 "actor": {"user_id": principal.email, "roles": list(principal.roles), "client": client},
                 "title": {"text": title, "source": source}, "decisions": [], "flags": []}
        if model:
            event["models"] = {"generator": model}
        self.audit.append(event)

    def _titles(self, email: str) -> dict[str, tuple[str, str]]:
        """conversation id -> (title, "generated" | "custom"), from the person's own `conversation_title` events: the
        newest custom name that was not cleared since, else the newest generated one."""
        generated: dict[str, str] = {}
        custom: dict[str, str] = {}
        for e in self.audit.events(user=email):
            if e.get("event_type") != "conversation_title":
                continue
            cid, title = e.get("conversation_id"), e.get("title") or {}
            if title.get("source") == "cleared":
                custom.pop(cid, None)
            elif title.get("source") == "custom" and title.get("text"):
                custom[cid] = title["text"]
            elif title.get("source") == "generated" and title.get("text"):
                generated[cid] = title["text"]
        return {**{c: (t, "generated") for c, t in generated.items()}, **{c: (t, "custom") for c, t in custom.items()}}

    def rename_conversation(self, principal: Principal, conversation_id: str, title: str | None) -> dict | None:
        """Name one of the caller's own conversations, or with None go back to its generated title. None when it is not
        theirs or does not exist: the two look alike. Raises ValueError for a title that is not one usable line."""
        if title is not None:
            cleaned = clean_title(title, MAX_CUSTOM_TITLE)
            if cleaned is None or len(title.strip()) > MAX_CUSTOM_TITLE or "\n" in title.strip():
                raise ValueError("title")
            title = cleaned
        if not any(self._conversation_of(e) == conversation_id for e in self._asks(principal.email)):
            return None
        self._title_event(principal, conversation_id, title, "custom" if title else "cleared", client=principal.client)
        return next(c for c in self.conversations(principal) if c["conversation_id"] == conversation_id)

    # -- nodes ----------------------------------------------------------------------------------
    def _identity(self, state: State) -> dict:
        return {"asker": self.resolver.resolve(state["principal"].email)}

    def _understand(self, state: State) -> dict:
        """The router and the rewrite: which skill applies, and what a follow-up is really asking for."""
        req, p = state["request"], state["principal"]
        history = self._history(p, req.conversation_id)
        query, rewritten = rewrite_query(req.question, history)
        skill = self.skills.select(query, p.roles, hint=req.skill_hint) if self.settings.skills else None
        profile = self._project_views(state["asker"]) if self.settings.projects else []
        resolved = self.projects.resolve(query, profile)
        return {"history": history, "search_query": query, "rewritten": rewritten, "skill": skill, "profile": profile,
                "resolved": resolved}

    def _project_views(self, asker: AskerIdentity) -> list[ProjectView]:
        """The registry as this person may see it: a project and each place under it only if they may open something there."""
        return self.projects.views(asker.tokens, self.index.parents_visible(sorted(asker.tokens)),
                                   container=self.container_resolver, token=self.token_resolver)

    def _history(self, principal: Principal, conversation_id: str | None) -> list[dict]:
        """The last few answered turns of the caller's own conversation, as memory for a follow-up.

        Memory is a derived artifact: `conversation()` shows an answer again only if the asker may still open every
        document it cited (its ACL label and a live PDP check), so a turn whose source was revoked comes back withheld and is
        dropped here. A refused, abstained or failed turn carries no answer worth remembering. Someone else's conversation
        and a nonexistent one look alike: no history."""
        if not conversation_id or not self.settings.memory_turns:
            return []
        conversation = self.conversation(principal, conversation_id)
        turns = [t for t in (conversation or {}).get("turns", [])
                 if not t["withheld"] and t["answer"] and not (t["refused"] or t["abstained"] or t["unavailable"])]
        out: list[dict] = []
        for t in turns[-self.settings.memory_turns:]:
            standalone, _ = rewrite_query(t["question"], out)          # a follow-up turn keeps the topic it was searched under
            out.append({"question": t["question"], "query": standalone, "answer": packetlib.sanitize(t["answer"])[0][:800]})
        return out

    def _retrieve(self, state: State) -> dict:
        req, asker = state["request"], state["asker"]
        skill = state.get("skill")
        wanted = [s for s in self.settings.sources if (not req.sources or s in req.sources) and (not skill or s in skill.sources)]
        unavailable = [s for s in wanted if s in asker.unavailable]
        searched = [s for s in wanted if s in asker.identities and s not in unavailable]
        terms = query_terms(state["search_query"])
        vector = model = None
        if self.embedder is not None and self.embedder.model not in ("none",):
            try:
                vector = self.embedder.embed([state["search_query"]])[0]
                model = self.embedder.model
            except Exception:                        # noqa: BLE001 - the keyword leg still runs
                vector = None
        candidates = hybrid_search(self.index, state["search_query"], asker.tokens, searched, vector=vector, model=model,
                                   candidates=self.settings.candidates, max_distance=self.settings.max_vector_distance)
        candidates = self._with_projects(candidates, state.get("resolved") or [], asker, searched)
        if skill and "my_issues" in skill.adds:
            candidates = self._with_my_issues(candidates, state["principal"], asker, searched)
        return {"searched": searched, "unavailable": unavailable, "terms": terms, "candidates": candidates}

    def _with_my_issues(self, candidates: list[Candidate], principal: Principal, asker: AskerIdentity,
                        searched: list[str]) -> list[Candidate]:
        """A question about the asker's own work ("my open tickets"): put the issues assigned to them first.

        Search matches a question's words against text, and no issue's text says "my". Whose an issue is comes from the
        connector's `assignee_email` field, compared with the asker's canonical email by plain code; an issue without that
        field falls back to the display name its text gives. Only rows the asker's tokens already reach are read, and every
        one added goes through the live permission check like any hit, so this adds nothing they could not open and says
        nothing about anyone else's issues. Open issues first, the earliest due date first, then the most recently changed."""
        if "jira" not in searched:
            return candidates
        mine = []
        for row in self.index.documents_visible(sorted(asker.tokens), "jira", TEAM_DOCS):
            fields = dict(row.fields or ())
            text = self._text(row.doc_id)
            if is_assignee(principal.email, fields) or (not fields.get("assignee_email") and principal.display_name
                                                        and assignee_of(text) == principal.display_name):
                done = (status_of(text) or "").lower() in DONE
                mine.append((done, fields.get("due") or "9999", row))
        mine.sort(key=lambda m: m[2].updated_at or "", reverse=True)
        mine.sort(key=lambda m: m[:2])
        by_id = {c.doc_id: c for c in candidates}
        top = max((c.score for c in candidates), default=0.0) + PROJECT_BOOST
        for place, (_, _, row) in enumerate(mine[:MY_ISSUES]):
            cand = by_id.get(row.doc_id)
            if cand is None:
                chunks = [c.chunk_id for c in self.index.chunks_of(row.doc_id)][:2]
                if not chunks:
                    continue
                cand = Candidate(row.doc_id, "jira", 0.0, chunks)
                candidates.append(cand)
            cand.score = top - place * 1e-4
        return sorted(candidates, key=lambda c: (-c.score, c.doc_id))

    def _with_projects(self, candidates: list[Candidate], resolved: list[ProjectView], asker: AskerIdentity,
                       searched: list[str]) -> list[Candidate]:
        """A question that names a project: raise the rank of documents inside it and add a few it missed. Boost, never
        filter: nothing is removed, and everything added goes through the live permission check like any hit."""
        if not resolved:
            return candidates
        places = {self.container_resolver(c) for v in resolved for c in v.places}
        for cand in candidates:
            row = self.index.document(cand.doc_id)
            if row is not None and row.parent_id in places:
                cand.score += PROJECT_BOOST
        have = {c.doc_id for c in candidates}
        floor = min((c.score for c in candidates), default=PROJECT_BOOST)
        for row in self.index.documents_under(sorted(places), sorted(asker.tokens), EXTRA_CANDIDATES + len(have)):
            if row.doc_id in have or source_of(row.doc_id) not in searched or len(have) >= len(candidates) + EXTRA_CANDIDATES:
                continue
            chunks = [c.chunk_id for c in self.index.chunks_of(row.doc_id)][:2]
            if chunks:
                candidates.append(Candidate(row.doc_id, source_of(row.doc_id), floor * 0.5, chunks))
                have.add(row.doc_id)
        return sorted(candidates, key=lambda c: (-c.score, c.doc_id))

    def _authorize(self, state: State) -> dict:
        asker = state["asker"]
        versions = {c.doc_id: self._indexed_version(c.doc_id) for c in state["candidates"]}
        decisions = self.pdp.check_many(asker, [(c.doc_id, versions[c.doc_id]) for c in state["candidates"]])
        allowed_ids = {d.doc_id for d in decisions if d.allowed}
        allowed = [c for c in state["candidates"] if c.doc_id in allowed_ids]
        return {"decisions": decisions, "allowed": allowed,
                "denied_docs": self._denied_set(asker, [c.doc_id for c in state["candidates"] if c.doc_id not in allowed_ids])}

    def _denied_set(self, asker: AskerIdentity, denied_candidates: list[str]) -> list[DeniedDoc]:
        """What the leak scan compares an answer with (fix F5): every live document the asker's tokens do not reach, the
        very ones the prefilter kept out of retrieval, plus any candidate the live check refused. Scanning only the
        refused candidates compared answers with almost nothing, because the prefilter removes forbidden documents first."""
        wanted = {d.doc_id: d for d in self.index.documents_hidden(sorted(asker.tokens))}
        for doc_id in denied_candidates:
            if doc_id not in wanted and (row := self.index.document(doc_id)) is not None:
                wanted[doc_id] = row
        out = []
        for doc_id, row in wanted.items():
            key = (doc_id, row.version)
            if key not in self._leak_cache:
                text = " ".join(ch.text for ch in self.index.chunks_of(doc_id))
                self._leak_cache[key] = prepare(DeniedDoc(doc_id, row.title, text))
            out.append(self._leak_cache[key])
        return out

    def _expand(self, state: State) -> dict:
        """Follow the stored links of the allowed hits, one hop.

        A link grants nothing: every target goes through the same live PDP check as a search hit, with the asker's own
        identity. A target that is denied, missing, deleted or in a source the asker is not searching is dropped with no
        trace in the response; a denied one joins the local leak-scan set. The check budget counts every target alike,
        so what the asker sees does not depend on how many denied documents a link list holds.
        """
        s, asker = self.settings, state["asker"]
        if not s.link_expansion or not state["allowed"]:
            return {"linked": {}}
        seen = {c.doc_id for c in state["candidates"]}
        targets: list[tuple[str, Candidate]] = []
        for parent in state["allowed"]:
            row = self.index.document(parent.doc_id)
            for raw in (row.links if row else ()):
                target = self.link_resolver(raw)
                if target in seen or source_of(target) not in state["searched"]:
                    continue
                seen.add(target)
                doc = self.index.document(target)
                if doc is None or doc.deleted or not self.index.chunks_of(target):
                    continue
                targets.append((target, parent))
        targets = targets[: s.link_checks]
        decisions = self.pdp.check_many(asker, [(t, self._indexed_version(t)) for t, _ in targets])
        linked: dict[str, str] = {}
        extra: list[Candidate] = []
        per_source: dict[str, int] = {}
        denied = list(state["denied_docs"])
        for (target, parent), decision in zip(targets, decisions, strict=True):
            if not decision.allowed:
                row = self.index.document(target)
                text = " ".join(ch.text for ch in self.index.chunks_of(target))
                denied.append(DeniedDoc(target, row.title if row else "", text))
                continue
            source = source_of(target)
            if len(extra) >= s.link_total or per_source.get(source, 0) >= s.link_per_source:
                continue
            per_source[source] = per_source.get(source, 0) + 1
            chunks = [c.chunk_id for c in self.index.chunks_of(target)][:2]
            extra.append(Candidate(target, source, parent.score * s.link_discount, chunks))
            linked[target] = parent.doc_id
        return {"linked": linked, "allowed": state["allowed"] + extra, "decisions": state["decisions"] + list(decisions),
                "denied_docs": denied}

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
            fields = dict(row.fields)
            refetched = 0
            unverified = False
            try:
                if connector is not None and connector.version(cand.doc_id) != chunks[0].source_version:
                    doc = connector.fetch(cand.doc_id)
                    text_by_chunk = {f"{cand.doc_id}#0": doc.body}
                    title, url, as_of, tokens = doc.title, doc.url, doc.updated_at, list(doc.acl.tokens)
                    fields = dict(doc.fields)
                    cand.chunk_ids = [f"{cand.doc_id}#0"]
                    refetched = 1
            except DocumentNotFound:
                return None, 0
            except Exception:                        # noqa: BLE001 - keep the indexed copy, which the PDP allowed, but say so (fix F9)
                unverified = True
            best = [cid for cid in cand.chunk_ids if cid in text_by_chunk] or [chunks[0].chunk_id]
            text = " ".join(text_by_chunk[cid] for cid in best[:2])
            ev = packetlib.make_evidence(best[0], cand.doc_id, cand.source, title, url, text, as_of, tokens, terms, cand.score,
                                         state.get("linked", {}).get(cand.doc_id), fields)
            if unverified:
                ev.flags.append(UNVERIFIED)
            return ev, refetched

        for ev, refetched in self._pool.map(one, state["allowed"]):
            stale += refetched
            if ev is not None:
                evidence.append(ev)
        return {"evidence": evidence, "stale_refetched": stale}

    def _generate(self, state: State) -> dict:
        p, asker = state["principal"], state["asker"]
        user_context = {"display_name": p.display_name or p.email, "sources": list(asker.sources()),
                        "projects": [v.project.name for v in state.get("profile", []) if v.member]}
        skill = state.get("skill")
        pkt = packetlib.build_packet(state["request_id"], user_context, state["request"].question,
                                     skill.name if skill else None, state["evidence"],
                                     # the asker's own issues are one source: the usual per-source cap would cut them to three
                                     per_source_quota=(max(self.settings.per_source_quota, MY_ISSUES)
                                                       if skill and "my_issues" in skill.adds else self.settings.per_source_quota),
                                     max_evidence=skill.max_evidence if skill else self.settings.max_evidence,
                                     procedure=skill.procedure if skill else None, history=state.get("history"),
                                     projects=[v.project.name for v in state.get("resolved", [])],
                                     standalone=state["search_query"] if state.get("rewritten") else None)
        flags = sorted({f for e in pkt["evidence"] for f in e["flags"]})
        return {"packet": pkt, "generated": self.generator.generate(pkt), "flags": flags}

    def _check(self, state: State) -> dict:
        allowed_ids = {e["doc_id"] for e in state["packet"]["evidence"]}
        checked = check(state["generated"], allowed_ids, state["denied_docs"])
        # Layer 1b (fix F23): what a claim says about a status or an assignee is compared with the field, by code.
        in_packet = {e.doc_id: e.fields for e in state["evidence"] if e.doc_id in allowed_ids and e.fields}
        by_field = check_fields(checked.claims, in_packet)
        if by_field.removed_claims:
            checked = CheckResult(render_answer(by_field.claims), by_field.claims, checked.removed_claims + by_field.removed_claims,
                                  checked.leak_hits, abstained=checked.abstained or not by_field.claims)
        evidence_text = {e.doc_id: f"{e.title}. {e.raw}" for e in state["evidence"]}
        context = {e.doc_id: lead_in(e.source, e.as_of) for e in state["evidence"]}   # packet metadata the generator also saw
        grounded = check_layer2(checked.claims, evidence_text, self.scorer, threshold=self.settings.grounding_threshold,
                                floor=self.settings.grounding_floor, context=context)
        if grounded.removed_claims or grounded.lower_confidence:
            checked = CheckResult(rebuild_answer(checked.answer, grounded.claims, grounded.removed_claims), grounded.claims,
                                  checked.removed_claims + grounded.removed_claims, checked.leak_hits,
                                  abstained=checked.abstained or not grounded.claims)
        return {"checked": checked, "grounded": grounded, "by_field": by_field}

    def _finalize(self, state: State) -> dict:
        req, checked, pkt = state["request"], state["checked"], state["packet"]
        skill = state.get("skill")
        proof = {d.doc_id: list(d.proof_path) for d in state["decisions"] if d.allowed}
        refused = not pkt["evidence"]
        # Fix F11: a model failure is not "no source supports an answer". It needs evidence to have been sent, so a
        # forbidden and a nonexistent document (no evidence, generator never called) still look identical.
        unavailable = (not refused) and state["generated"].unavailable
        abstained = (not refused) and (not unavailable) and checked.abstained
        shown_ids = {d for c in checked.claims for d in c["citations"]} if not (refused or abstained or unavailable) else set()
        by_id = {e["doc_id"]: e for e in pkt["evidence"]}
        excerpts = {e.doc_id: e.raw for e in state["evidence"]}
        citations = [{"doc_id": d, "title": by_id[d]["title"], "url": by_id[d]["url"], "source": by_id[d]["source"],
                      "as_of": by_id[d]["as_of"], "why_visible": proof.get(d, []), "excerpt": excerpts.get(d, "")[:280],
                      "via_link_from": by_id[d]["via_link_from"], "unverified": UNVERIFIED in by_id[d]["flags"]}
                     for d in by_id if d in shown_ids]
        unverified_sources = sorted({c["source"] for c in citations if c["unverified"]})
        if refused:
            answer, claims = REFUSAL, []
        elif unavailable:
            answer, claims = UNAVAILABLE, []
        elif abstained:
            answer, claims = ABSTAIN, []
        else:
            answer, claims = checked.answer, checked.claims
        conversation_id = req.conversation_id or f"c_{uuid.uuid4().hex[:6]}"
        response = {
            "request_id": state["request_id"], "conversation_id": conversation_id, "answer": answer, "claims": claims,
            "citations": citations, "refused": refused, "abstained": abstained, "generator_unavailable": unavailable,
            "freshness": {"oldest_source_as_of": min((c["as_of"] for c in citations if c["as_of"]), default=None),
                          "stale_refetched": state["stale_refetched"], "unverified_sources": unverified_sources,
                          "per_source": self._per_source(state["unavailable"], unverified_sources)},
            "skill": skill.name if skill else None,
            "coverage": {s: {"searched": s in state["searched"], "shown": sum(c["source"] == s for c in citations)}
                         for s in SOURCES},
            "grounding": None if (refused or abstained or unavailable) else checked.grounding,
            "policy_version": self.pdp.policy_version, "unavailable_sources": state["unavailable"], "clarify": None,
        }
        return {"response": response}

    def _per_source(self, unavailable: list[str], unverified: list[str] = ()) -> dict:
        runs = {r.source: r for r in (self.store.runs() if self.store is not None else [])}
        now = datetime.now(UTC)
        out = {}
        for s in SOURCES:
            run = runs.get(s)
            last = run.last_ok_at if run else None
            status = "ok"
            if s in unavailable:
                status = "unavailable"
            elif s in unverified:                    # its live version check failed: an indexed copy was served unchecked (F9)
                status = "unverified"
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
        skill = state.get("skill")
        salt = self.settings.denied_id_salt
        cited = [c["doc_id"] for c in resp["citations"]]
        label = acl_label(e["acl_label"] for e in state["packet"]["evidence"] if e["doc_id"] in cited)
        event = {
            "request_id": state["request_id"], "event_type": "ask", "conversation_id": resp["conversation_id"],
            "actor": {"user_id": p.email, "roles": list(p.roles), "client": p.client},
            "query": {"text": req.question, "skill": skill.name if skill else None,
                      "skill_version": skill.version if skill else None, "skill_sha256": skill.sha256 if skill else None,
                      "rewritten": state["search_query"] if state.get("rewritten") else None,
                      "projects": [v.project.project_id for v in state.get("resolved", [])]},
            "decisions": [d.audit_view(salt) for d in state["decisions"]],
            "answer": {"text": resp["answer"], "sha256": sha256_text(resp["answer"]), "citations": cited,
                       "refused": resp["refused"], "abstained": resp["abstained"],
                       "unavailable": resp["generator_unavailable"], "acl_label": label},
            "checks": {**checked.status, "leak_scan_docs": len(state["denied_docs"]),
                       "generator": "unavailable" if resp["generator_unavailable"] else "ok",
                       "fields": state["by_field"].status, "field_mismatches": state["by_field"].mismatches,
                       "grounding_model": state["grounded"].status,
                       "grounding_scores": state["grounded"].scores,
                       "grounding_lower_confidence": state["grounded"].lower_confidence,
                       "leak_hits": [hash_denied(d, salt) for d in checked.leak_hits]},
            "models": {"generator": self.generator.model, "checker": state["grounded"].model,
                       "embedding": f"{self.embedder.model}@{self.embedder.version}" if self.embedder else "none"},
            "flags": state["flags"],
            "links_followed": [{"doc_id": t, "via_link_from": f} for t, f in sorted(state.get("linked", {}).items())],
            "latency_ms": int((time.monotonic() - state["started"]) * 1000),
        }
        if req.routine_id:
            event["routine_id"] = req.routine_id
        self.audit.append(event)
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

    # -- search and get_source: the same checks as `ask`, with no model in the loop (used by the MCP server) ---------------
    def _pad(self, started: float) -> None:
        """Hold a reply back to `floor_latency_ms`, so a forbidden and a nonexistent document take alike (as `ask` does)."""
        remaining = self.settings.floor_latency_ms / 1000 - (time.monotonic() - started)
        if remaining > 0:
            time.sleep(remaining)

    def _log_tool(self, principal: Principal, event_type: str, text: str, decisions: list[Decision], extra: dict, started: float) -> str:
        request_id = f"{event_type[:4]}_{uuid.uuid4().hex[:8]}"
        self.audit.append({
            "request_id": request_id, "event_type": event_type,
            "actor": {"user_id": principal.email, "roles": list(principal.roles), "client": principal.client},
            "query": {"text": text, "skill": None},
            "decisions": [d.audit_view(self.settings.denied_id_salt) for d in decisions], **extra,
            "flags": [], "latency_ms": int((time.monotonic() - started) * 1000)})
        return request_id

    def search(self, principal: Principal, query: str, *, sources: list[str] | None = None, limit: int = 5) -> list[dict]:
        """Documents the person may read that match `query`: the access prefilter, then the live check on every candidate,
        then a cleaned snippet. Nothing is said about what was filtered out, and an empty list means "nothing you can see"."""
        started = time.monotonic()
        limit = max(1, min(int(limit), 10))
        asker = self.resolver.resolve(principal.email)
        wanted = [src for src in self.settings.sources
                  if (not sources or src in sources) and src in asker.identities and src not in asker.unavailable]
        terms = query_terms(query)
        vector = model = None
        if self.embedder is not None and self.embedder.model not in ("none",):
            try:
                vector, model = self.embedder.embed([query])[0], self.embedder.model
            except Exception:                        # noqa: BLE001 - the keyword leg still runs
                vector = None
        candidates = hybrid_search(self.index, query, asker.tokens, wanted, vector=vector, model=model, candidates=limit * 2,
                                   max_distance=self.settings.max_vector_distance)
        decisions = self.pdp.check_many(asker, [(c.doc_id, self._indexed_version(c.doc_id)) for c in candidates])
        allowed = {d.doc_id: d for d in decisions if d.allowed}
        hits: list[dict] = []
        for c in candidates:
            row, chunks = self.index.document(c.doc_id), self.index.chunks_of(c.doc_id)
            if c.doc_id not in allowed or row is None or not chunks:
                continue
            text = " ".join(ch.text for ch in chunks if ch.chunk_id in c.chunk_ids) or chunks[0].text
            clean, flags = packetlib.sanitize(packetlib.snippet(text, terms))
            hits.append({"doc_id": c.doc_id, "title": row.title, "url": row.url, "source": row.source, "as_of": row.updated_at,
                         "snippet": clean, "why_visible": list(allowed[c.doc_id].proof_path), "flags": flags})
            if len(hits) == limit:
                break
        self._log_tool(principal, "search", query, decisions, {"result": {"doc_ids": [h["doc_id"] for h in hits]}}, started)
        return hits

    def get_source(self, principal: Principal, doc_id: str, *, max_chars: int = 8000) -> dict | None:
        """One document's text, or None. A forbidden and a nonexistent document give the same None after the same delay.
        The text is read live from the source when it can be (so it is current) and cleaned of instruction-like sentences."""
        started = time.monotonic()
        asker = self.resolver.resolve(principal.email)
        decision = self.pdp.check(asker, doc_id, self._indexed_version(doc_id))
        result = None
        if decision.allowed:
            row, chunks = self.index.document(doc_id), self.index.chunks_of(doc_id)
            title, url, as_of = (row.title, row.url, row.updated_at) if row else (doc_id, "", None)
            text = " ".join(ch.text for ch in chunks)
            connector = self.connectors.get(source_of(doc_id))
            try:
                if connector is not None:
                    live = connector.fetch(doc_id)
                    title, url, as_of, text = live.title, live.url, live.updated_at, live.body
            except Exception:                        # noqa: BLE001 - keep the indexed copy, which the PDP allowed
                pass
            if text:
                clean, flags = packetlib.sanitize(" ".join(text.split()))
                result = {"doc_id": doc_id, "title": title, "url": url, "source": source_of(doc_id), "as_of": as_of,
                          "text": clean[:max_chars], "truncated": len(clean) > max_chars,
                          "why_visible": list(decision.proof_path), "flags": flags}
        self._log_tool(principal, "get_source", f"get_source {'allowed' if result else 'refused'}", [decision], {}, started)
        self._pad(started)
        return result

    def project_profile(self, principal: Principal) -> list[dict]:
        """The caller's projects, for `/v1/projects` and My Work: only what they may open something in."""
        return [v.as_json() for v in self._project_views(self.resolver.resolve(principal.email))]

    def team_focus(self, principal: Principal, names: dict[str, str] | None = None) -> list[dict]:
        """Team focus (`/v1/team`): the projects the caller is a member of, counted only from documents they may open right
        now (prefilter, then the live check), so nothing in it reflects a document they cannot see."""
        asker = self.resolver.resolve(principal.email)
        views = [v for v in self._project_views(asker) if v.member] if self.settings.projects else []
        if not views:
            return []
        parents = sorted({self.container_resolver(place) for v in views for place in v.places})
        rows = self.index.documents_under(parents, sorted(asker.tokens), limit=TEAM_DOCS)
        ok = {d.doc_id for d in self.pdp.check_many(asker, [(r.doc_id, r.version) for r in rows]) if d.allowed}
        return team_focus(views, [r for r in rows if r.doc_id in ok], names=names, container=self.container_resolver,
                          me=(names or {}).get(principal.email) or principal.display_name, email=principal.email,
                          text=self._text)

    def _text(self, doc_id: str) -> str:
        return "\n".join(c.text for c in self.index.chunks_of(doc_id))

    def my_issues(self, principal: Principal, rows: list, names: dict[str, str] | None = None) -> list[dict]:
        """The Jira issues among `rows` (index rows the caller may open) as `/v1/mywork` lists them (api.md 0.3)."""
        me = (names or {}).get(principal.email) or principal.display_name
        return [issue_view(r, self._text(r.doc_id), me=me, email=principal.email) for r in rows if r.source == "jira"]

    # -- the one write: moving one's own Jira issue (api.md 0.3, acl-model.md "Writes") --------------------------------
    def move_issue(self, principal: Principal, doc_id: str, status: str) -> tuple[int, dict]:
        """Move an issue to `status` in Jira, as the caller, if they may open it and are its assignee. Returns the HTTP
        status and body. No model takes part, and nothing here is reachable from the LLM plane.

        The order matters: the read check comes first and answers like a missing issue, so nothing after it can say
        anything about an issue the caller cannot open. Jira holds the status; the Brain keeps none, and after a move it
        hands the issue Jira returned to ingestion so the index says the same."""
        started = time.monotonic()
        request_id = f"move_{uuid.uuid4().hex[:8]}"
        asker = self.resolver.resolve(principal.email)
        decision = self.pdp.check(asker, doc_id, self._indexed_version(doc_id))
        connector = self.connectors.get("jira")
        transition: dict | None = None
        code, body = 404, {"detail": "no such issue"}
        if source_of(doc_id) == "jira" and decision.allowed and connector is not None:
            transition = {"to": status, "outcome": "source_unavailable"}
            code, body = 503, {"error": {"code": "source_unavailable", "message": "Jira could not be reached. Nothing was changed."}}
            try:
                live = connector.fetch(doc_id)                 # whose it is and where it stands now, from Jira itself
                transition["from"] = live.fields.get("status") or status_of(live.body)
                if not is_assignee(principal.email, live.fields):
                    transition["outcome"] = "refused_not_assignee"
                    code, body = 403, {"error": {"code": "not_assignee", "message": "Only the assignee can move this issue."}}
                elif (transition["from"] or "").lower() == status.strip().lower():
                    transition["outcome"] = "unchanged"
                    code, body = 200, self._moved(request_id, live, indexed=True)
                elif not hasattr(connector, "transition"):
                    pass                                       # a read-only connector: nothing can be moved through it
                else:
                    moved = connector.transition(asker.identity("jira"), doc_id, status)
                    indexed = self._reindex(moved)
                    transition.update(outcome="moved", to=moved.fields.get("status") or status, indexed=indexed,
                                      doc_version_after=moved.version)
                    code, body = 200, self._moved(request_id, moved, indexed=indexed)
            except DocumentNotFound:                           # gone since the check: the same answer as never there
                transition, code, body = None, 404, {"detail": "no such issue"}
            except TransitionRefused:
                transition["outcome"] = "refused_by_source"
                code, body = 409, {"error": {"code": "transition_refused", "message": "Jira does not allow that move."}}
            except Exception:                                  # noqa: BLE001 - unknown outcome: say so, and never retry a write
                pass
        event = {"request_id": request_id, "event_type": "issue_transition",
                 "actor": {"user_id": principal.email, "roles": list(principal.roles), "client": principal.client},
                 "decisions": [decision.audit_view(self.settings.denied_id_salt)], "flags": [],
                 "latency_ms": int((time.monotonic() - started) * 1000)}
        if transition is not None:
            event["transition"] = transition
        self.audit.append(event)
        self._pad(started)
        return code, body

    @staticmethod
    def _moved(request_id: str, doc: Document, *, indexed: bool) -> dict:
        return {"request_id": request_id, "doc_id": doc.doc_id, "status": doc.fields.get("status") or status_of(doc.body),
                "updated_at": doc.updated_at, "indexed": indexed}

    def _reindex(self, doc: Document) -> bool:
        if self.reindex is None:
            return False
        try:
            self.reindex(doc)
        except Exception:                                      # noqa: BLE001 - the move happened; the next sync catches the index up
            return False
        self.pdp.invalidate_doc(doc.doc_id)
        return True

    def visible_documents(self, principal: Principal, source: str | None = None, limit: int = 100) -> list:
        """Index rows the person may read right now: prefilter, then the live check on each."""
        asker = self.resolver.resolve(principal.email)
        rows = self.index.documents_visible(sorted(asker.tokens), source, limit)
        decisions = self.pdp.check_many(asker, [(r.doc_id, r.version) for r in rows])
        ok = {d.doc_id for d in decisions if d.allowed}
        return [r for r in rows if r.doc_id in ok]

    def alerts(self, principal: Principal) -> list[dict]:
        """Answers whose cited documents changed since, for documents the person may still see.

        Rebuilt from the person's own `ask` events, which record the version of every document each answer cited, so alerts
        survive a restart (fix F16). Only the person's latest ask of each question counts: once they have asked again, the
        older answers are superseded, and an alert on them would send them to "ask again" for something they already did
        (11 Oct, the real-stack run: 85 alerts on My Work, 46 of them for one question asked 46 times)."""
        out = []
        asker = self.resolver.resolve(principal.email)
        latest: dict[str, dict] = {}
        for event in self._asks(principal.email):                    # oldest first, so the last one per question wins
            latest[" ".join(event["query"]["text"].lower().split())] = event
        for event in latest.values():
            cited = set((event.get("answer") or {}).get("citations", []))
            versions = {d["doc_id"]: d.get("doc_version") for d in event.get("decisions", [])
                        if d.get("allowed") and d.get("doc_id") in cited}
            for doc_id, then in versions.items():
                row = self.index.document(doc_id)
                now_version = self._indexed_version(doc_id)
                if row is None or now_version is None or now_version == then:
                    continue
                if not self.pdp.check(asker, doc_id, now_version).allowed:
                    continue
                out.append({"request_id": event["request_id"], "question": event["query"]["text"], "changed_doc": doc_id,
                            "changed_title": row.title, "changed_at": row.updated_at,
                            "summary": f"{row.title} changed after your answer"})
        return out

    # -- conversations: rebuilt from the audit log, so they survive a restart (api.md 0.2) -----------
    def _asks(self, email: str) -> list[dict]:
        """The person's own `ask` events, oldest first. An event logged before conversations were recorded
        (no `conversation_id`) counts as a conversation of its own."""
        return [e for e in self.audit.events(user=email) if e.get("event_type") == "ask"]

    @staticmethod
    def _conversation_of(event: dict) -> str:
        return event.get("conversation_id") or event["request_id"]

    def conversations(self, principal: Principal) -> list[dict]:
        convs: dict[str, dict] = {}
        for e in self._asks(principal.email):
            cid = self._conversation_of(e)
            c = convs.setdefault(cid, {"conversation_id": cid, "title": e["query"]["text"], "title_source": "question"})
            c["last_asked_at"] = e["ts"]
        for cid, (title, source) in self._titles(principal.email).items():
            if cid in convs:
                convs[cid].update(title=title, title_source=source)
        return sorted(convs.values(), key=lambda c: c["last_asked_at"], reverse=True)

    def conversation(self, principal: Principal, conversation_id: str) -> dict | None:
        """One of the caller's conversations with every turn, or None: someone else's and a nonexistent one look alike.
        An answer is shown again only if the asker may still open every document it cited (its label and a live
        check); otherwise the turn keeps its question and `withheld` is set."""
        asks = [e for e in self._asks(principal.email) if self._conversation_of(e) == conversation_id]
        if not asks:
            return None
        asker = self.resolver.resolve(principal.email)
        title, source = self._titles(principal.email).get(conversation_id, (asks[0]["query"]["text"], "question"))
        return {"conversation_id": conversation_id, "title": title, "title_source": source, "last_asked_at": asks[-1]["ts"],
                "turns": [self._turn(e, asker) for e in asks]}

    def _turn(self, event: dict, asker: AskerIdentity) -> dict:
        answer = event.get("answer") or {}
        cited = list(answer.get("citations", []))
        decisions = self.pdp.check_many(asker, [(d, self._indexed_version(d)) for d in cited])
        ok = may_serve(asker.tokens, answer.get("acl_label", [])) and all(d.allowed for d in decisions)
        proof = {d["doc_id"]: list(d.get("proof_path", [])) for d in event.get("decisions", []) if d.get("allowed")}
        citations = []
        for doc_id in cited if ok else []:
            row = self.index.document(doc_id)
            citations.append({"doc_id": doc_id, "title": row.title if row else doc_id, "url": row.url if row else "",
                              "source": row.source if row else doc_id.split(":", 1)[0],
                              "as_of": row.updated_at if row else None, "why_visible": proof.get(doc_id, [])})
        return {"request_id": event["request_id"], "asked_at": event.get("ts"), "question": event["query"]["text"],
                "skill": event["query"].get("skill"), "answer": answer.get("text") if ok else None, "withheld": not ok,
                "citations": citations, "refused": bool(answer.get("refused")), "abstained": bool(answer.get("abstained")),
                "unavailable": bool(answer.get("unavailable")), "routine_id": event.get("routine_id")}

    def stages(self) -> tuple[str, ...]:
        return STAGES

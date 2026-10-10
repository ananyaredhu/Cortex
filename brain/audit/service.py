"""Audit queries under RBAC, `/verify`, and replay (audit-event-schema.md, "Querying"; api.md 0.2).

Only the compliance role queries; every query is itself logged. An answer's text is shown to the officer only when
they may see every document it cites (checked live through the PDP); the hash is always shown. Titles in a replay
follow the same rule.
"""
import re
from datetime import UTC, datetime

from brain.auth import Principal
from brain.pipeline.graph import Brain
from brain.policy.pdp import hash_denied

from . import chain
from .agent import AuditPlan, Clarify, Directory, PlanError, Planner, planner_from_settings, space_matches, validate_plan
from .store import AuditStore

CLIENT = "ui"
TITLE_NOISE = frozenset("the and for with from last".split())


class AuditService:
    def __init__(self, store: AuditStore, brain: Brain) -> None:
        self._store = store
        self._brain = brain
        self.planner: Planner = planner_from_settings(brain.settings)

    def record(self, event: dict) -> dict:
        return self._store.append(event)

    # -- verify ---------------------------------------------------------------------------------
    def verify(self) -> dict:
        """Integrity of the whole chain. With a configured `AUDIT_SIGNING_KEY` every checkpoint must verify against that
        key (`signer_pinned`: true). Without one each checkpoint is checked against the key it carries, which proves the
        chain was not edited but not who signed it, so a restart no longer looks like tampering (fix F3)."""
        pinned = getattr(self._store, "pinned_public_key_hex", None)
        out = chain.verify(self._store.all(), public_key_hex=pinned).as_json()
        out["signer_pinned"] = pinned is not None
        return out

    # -- query ----------------------------------------------------------------------------------
    def query(self, officer: Principal, body: dict) -> dict:
        f = body.get("filter") or {}
        out = self._events(officer, f)
        self._log_query(officer, body.get("question") or _canon(f))
        return out

    def _events(self, officer: Principal, f: dict) -> dict:
        wanted = self._selector(f)
        events = [e for e in self._store.events(user=(f.get("user") or "").lower() or None) if wanted(e)]
        shown = [self._for_officer(e, officer) for e in events]
        return {"events": shown, "count": len(shown)}

    def _log_query(self, officer: Principal, text: str, plan: dict | None = None) -> None:
        event = {"request_id": f"aq_{self._store.count() + 1}", "event_type": "audit_query",
                 "actor": {"user_id": officer.email, "roles": list(officer.roles), "client": officer.client},
                 "query": {"text": text, "skill": None}, "decisions": [], "flags": []}
        if plan is not None:
            event["detail"] = {"plan": plan}
        self.record(event)

    def _selector(self, f: dict):
        """A predicate over events for the filter. A denied decision keeps only a salted hash of its document, so a `space` or
        `doc` filter also matches the hashes of the documents it covers: an auditor can find the attempts on a document."""
        salt = self._brain.settings.denied_id_salt
        directory = self._directory() if (f.get("space") or f.get("doc")) else Directory()
        docs = {f["doc"]} if f.get("doc") else set()
        if f.get("space"):
            docs |= {d for d in directory.docs if space_matches(d, f["space"])}
        ids, hashes = docs, {hash_denied(d, salt) for d in docs}
        needs_docs = bool(f.get("space") or f.get("doc"))
        title_terms = _title_terms(directory.docs.get(f["doc"], "")) if f.get("doc") else set()
        decision, since, until = f.get("decision"), f.get("from"), f.get("to")

        def match(e: dict) -> bool:
            decisions = e.get("decisions", [])
            refused = _refused(e)
            if needs_docs and not any(d.get("doc_id") in ids or d.get("doc_id_hash") in hashes
                                      or (f.get("space") and space_matches(d.get("doc_id", ""), f["space"])) for d in decisions):
                # A refused question never reached the document, so no decision names it; the words of the question do.
                if not (refused and title_terms and len(title_terms & _words(e.get("query", {}).get("text", ""))) >= 2):
                    return False
            if decision == "allowed" and not any(d.get("allowed") for d in decisions):
                return False
            if decision == "denied" and not (refused or any(d.get("allowed") is False for d in decisions)):
                return False
            if since and e.get("ts", "") < since:
                return False
            return not (until and e.get("ts", "") > until)
        return match

    def _directory(self) -> Directory:
        """What the planner may know: people, spaces and document titles. No audit events."""
        brain, docs = self._brain, {}
        if brain.store is not None:
            for source in brain.settings.sources:
                for doc_id in brain.store.live_doc_ids(source):
                    row = brain.index.document(doc_id)
                    docs[doc_id] = row.title if row else doc_id
        return Directory(set(brain._identity_seen), {sp for d in docs if (sp := _space_of(d))}, docs)

    # -- the audit agent ------------------------------------------------------------------------
    def ask(self, officer: Principal, question: str, *, planner: Planner | None = None, now: datetime | None = None) -> dict:
        """A question in words -> a validated plan -> the same code paths the structured queries use (backlog T9).

        The planner sees the question and the directory (people, spaces, titles), never an audit event. The summary is
        written here from the result, not by a model. A question that cannot be planned comes back as `clarify`."""
        directory = self._directory()
        planned = (planner or self.planner).plan(question, directory, now or datetime.now(UTC))
        plan, clarify = None, None
        if isinstance(planned, Clarify):
            clarify = planned.message
        else:
            try:
                plan = validate_plan(planned.as_json(), directory)
            except PlanError as exc:
                clarify = f"{exc}. Try naming a person, a space, a document or a time."
        self._log_query(officer, question, plan.as_json() if plan else {"clarify": clarify})
        base = {"question": question, "plan": plan.as_json() if plan else None, "clarify": clarify}
        if plan is None:
            return {**base, "summary": None, "result": None}
        if plan.kind == "verify":
            result = self.verify()
            summary = (f"The log is intact: {result['checked']} events and {result['checkpoints']} signed checkpoints verified."
                       if result.get("ok") else f"The log is NOT intact: first broken event is {result['first_broken_seq']} "
                       f"({result['reason']}).")
        elif plan.kind == "time_travel":
            result = self._time_travel(officer, plan.user, plan.at)
            summary = _time_travel_summary(result)
        else:
            result = self._events(officer, plan.filter)
            summary = _query_summary(result["events"], plan)
        return {**base, "summary": summary, "result": result}

    def _may_see_all(self, officer: Principal, doc_ids: list[str]) -> bool:
        if not doc_ids:
            return True
        asker = self._brain.resolver.resolve(officer.email)
        return all(d.allowed for d in self._brain.pdp.check_many(asker, [(doc_id, None) for doc_id in doc_ids]))

    def _for_officer(self, event: dict, officer: Principal) -> dict:
        answer = event.get("answer")
        if not answer or self._may_see_all(officer, list(answer.get("citations", []))):
            return event
        return {**event, "answer": {**{k: v for k, v in answer.items() if k != "text"}, "text": None, "text_withheld": True}}

    # -- replay ---------------------------------------------------------------------------------
    def replay(self, officer: Principal, request_id: str) -> dict | None:
        asks = [e for e in self._store.events(request_id=request_id) if e.get("event_type") == "ask"]
        if not asks:
            return None
        event = asks[0]
        answer = event.get("answer", {})
        cited = list(answer.get("citations", []))
        officer_sees = {c for c, d in zip(cited, self._brain.pdp.check_many(self._brain.resolver.resolve(officer.email),
                                                                           [(c, None) for c in cited]), strict=True) if d.allowed}
        logged = {d["doc_id"]: d for d in event.get("decisions", []) if d.get("allowed")}
        asker = self._brain.resolver.resolve(event["actor"]["user_id"])
        now_decisions = {d.doc_id: d for d in self._brain.pdp.check_many(asker, [(c, None) for c in cited])}

        def cite(doc_id: str) -> dict:
            if doc_id not in officer_sees:
                return {"doc_id": doc_id, "restricted": True}
            row = self._brain.index.document(doc_id)
            return {"doc_id": doc_id, "title": row.title if row else None, "url": row.url if row else None}

        differences = []
        for doc_id in cited:
            row = self._brain.index.document(doc_id)
            if row is None or row.deleted:
                differences.append({"doc_id": doc_id, "change": "deleted"})
            elif not now_decisions[doc_id].allowed:
                differences.append({"doc_id": doc_id, "change": "revoked"})
            elif logged.get(doc_id, {}).get("doc_version") not in (None, row.version):
                differences.append({"doc_id": doc_id, "change": "edited"})
        text_ok = all(c in officer_sees for c in cited)
        then = {"answer": answer.get("text") if text_ok else None, "answer_sha256": answer.get("sha256"),
                "citations": [cite(c) for c in cited], "policy_version": event.get("decisions", [{}])[0].get("policy_version")
                if event.get("decisions") else None}
        now = {"answer": None, "citations": [cite(c) for c in cited if now_decisions[c].allowed and c not in
                                             {d["doc_id"] for d in differences if d["change"] == "deleted"}],
               "policy_version": self._brain.pdp.policy_version}
        return {"request_id": request_id, "then": then, "now": now, "differences": differences}

    # -- time travel ----------------------------------------------------------------------------
    def time_travel(self, officer: Principal, user: str, at: str) -> dict:
        """The query, logged (api.md, `/audit/time-travel`)."""
        out = self._time_travel(officer, user, at)
        self._log_query(officer, f"time-travel user={user.strip().lower()} at={at}")
        return out

    def _time_travel(self, officer: Principal, user: str, at: str) -> dict:
        """What `user` could open at time `at`, and what changed since (api.md, `/audit/time-travel`).

        A document was visible if its ACL snapshot valid at `at` shares a token with the person's token set at `at`. The
        ACL side is ingestion's bi-temporal `acl_snapshots`. The person's side is the `identity_snapshot` events this
        Brain wrote into the audit log whenever their token set changed: the last one at or before `at`. Before the
        first one the person's tokens are unknown, and the answer says so instead of guessing. Titles follow the replay
        rule: shown only for documents the officer may open. Raises ValueError for an unreadable `at`.
        """
        when = parse_time(at)
        email = user.strip().lower()
        history = [e for e in self._store.all() if e.get("event_type") == "identity_snapshot" and e["identity"]["user"] == email]
        known = [e for e in history if parse_time(e["ts"]) <= when]
        tokens = frozenset(known[-1]["identity"]["tokens"]) if known else frozenset()
        then = self._visible(tokens, when) if known else {}
        now = self._visible(self._brain.resolver.resolve(email).tokens, None)
        lost, gained = sorted(set(then) - set(now)), sorted(set(now) - set(then))
        shown = self._officer_view(officer, set(then) | set(lost) | set(gained))

        def row(doc_id: str, via: list[str] | None = None) -> dict:
            out = shown(doc_id)
            return out if out.get("restricted") or via is None else {**out, "via": via}

        return {"user": email, "at": when.isoformat(timespec="seconds").replace("+00:00", "Z"),
                "identity": {"known": bool(known), "recorded_at": known[-1]["ts"] if known else None,
                             "first_recorded_at": history[0]["ts"] if history else None, "tokens": sorted(tokens)},
                "could_see": [row(d, then[d]) for d in sorted(then)],
                "changed_since": {"lost": [row(d) for d in lost], "gained": [row(d) for d in gained]}}

    def _visible(self, tokens: frozenset[str], when: datetime | None) -> dict[str, list[str]]:
        """doc_id -> the tokens that opened it, for every indexed document whose ACL at `when` (None: now) meets `tokens`."""
        store = self._brain.store
        if store is None:
            return {}
        out: dict[str, list[str]] = {}
        for source in self._brain.settings.sources:
            for doc_id in sorted(store.live_doc_ids(source)):
                for snap in store.snapshots_of(doc_id):
                    start, end = parse_time(snap.valid_from), parse_time(snap.valid_to) if snap.valid_to else None
                    current = end is None if when is None else start <= when and (end is None or when < end)
                    if current and (hit := sorted(tokens & set(snap.tokens))):
                        out[doc_id] = hit
        return out

    def _officer_view(self, officer: Principal, doc_ids: set[str]):
        ids = sorted(doc_ids)
        asker = self._brain.resolver.resolve(officer.email)
        may = {d for d, dec in zip(ids, self._brain.pdp.check_many(asker, [(d, None) for d in ids]), strict=True) if dec.allowed}

        def view(doc_id: str) -> dict:
            if doc_id not in may:
                return {"doc_id": doc_id, "restricted": True}
            row = self._brain.index.document(doc_id)
            return {"doc_id": doc_id, "title": row.title if row else None, "url": row.url if row else None,
                    "source": doc_id.split(":", 1)[0]}
        return view


def _refused(event: dict) -> bool:
    """An ask the Brain refused: nothing the person may open matched, so it is a denial as far as they are concerned."""
    return event.get("event_type") == "ask" and bool((event.get("answer") or {}).get("refused"))


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _title_terms(title: str) -> set[str]:
    return {w for w in _words(title) if len(w) >= 3 and w not in TITLE_NOISE}


def _space_of(doc_id: str) -> str | None:
    """`confluence:PAY` for a page, `jira:DBMIG` for a ticket, `slack:C_DBMIG` for a thread; None for a Drive file."""
    source, _, rest = doc_id.partition(":")
    if "/" in rest:
        return f"{source}:{rest.split('/', 1)[0]}"
    if source == "jira" and "-" in rest:
        return f"{source}:{rest.rsplit('-', 1)[0]}"
    return None


def _query_summary(events: list[dict], plan: AuditPlan) -> str:
    if not events:
        return f"No events match. I looked for: {plan.understood}"
    kinds: dict[str, int] = {}
    allowed = denied = 0
    docs: set[str] = set()
    for e in events:
        kinds[e.get("event_type", "?")] = kinds.get(e.get("event_type", "?"), 0) + 1
        for d in e.get("decisions", []):
            allowed += bool(d.get("allowed"))
            denied += not d.get("allowed")
            if d.get("allowed") and d.get("doc_id"):
                docs.add(d["doc_id"])
    parts = ", ".join(f"{n} {k}" for k, n in sorted(kinds.items(), key=lambda kv: -kv[1]))
    first, last = events[0].get("ts", "")[:19], events[-1].get("ts", "")[:19]
    refused = sum(_refused(e) for e in events)
    return (f"{len(events)} events ({parts}) between {first} and {last}. "
            f"Documents opened: {len(docs)}. Access decisions: {allowed} allowed, {denied} denied."
            + (f" Refused questions: {refused} (the person never reached a document)." if refused else ""))


def _time_travel_summary(result: dict) -> str:
    who, at = result["user"], result["at"]
    if not result["identity"]["known"]:
        first = result["identity"]["first_recorded_at"]
        return (f"The Brain has no record of {who}'s groups at {at}" +
                (f"; its record starts at {first}." if first else "; it has never seen them."))
    lost, gained = result["changed_since"]["lost"], result["changed_since"]["gained"]
    return (f"At {at}, {who} could open {len(result['could_see'])} documents. Since then they lost access to {len(lost)} and "
            f"gained access to {len(gained)}.")


def parse_time(value: str) -> datetime:
    """An ISO 8601 time as an aware UTC datetime; a time with no zone is read as UTC."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _canon(f: dict) -> str:
    import json
    return json.dumps(f, sort_keys=True)

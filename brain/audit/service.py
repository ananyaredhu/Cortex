"""Audit queries under RBAC, `/verify`, and replay (audit-event-schema.md, "Querying"; api.md 0.2).

Only the compliance role queries; every query is itself logged. An answer's text is shown to the officer only when
they may see every document it cites (checked live through the PDP); the hash is always shown. Titles in a replay
follow the same rule.
"""
from brain.auth import Principal
from brain.pipeline.graph import Brain

from . import chain
from .store import AuditStore

CLIENT = "ui"


class AuditService:
    def __init__(self, store: AuditStore, brain: Brain) -> None:
        self._store = store
        self._brain = brain

    def record(self, event: dict) -> dict:
        return self._store.append(event)

    # -- verify ---------------------------------------------------------------------------------
    def verify(self) -> dict:
        key = getattr(self._store, "public_key_hex", None)
        return chain.verify(self._store.all(), public_key_hex=key).as_json()

    # -- query ----------------------------------------------------------------------------------
    def query(self, officer: Principal, body: dict) -> dict:
        f = body.get("filter") or {}
        events = self._store.events(user=f.get("user"))
        events = [e for e in events if self._matches(e, f)]
        shown = [self._for_officer(e, officer) for e in events]
        self.record({"request_id": f"aq_{self._store.count() + 1}", "event_type": "audit_query",
                     "actor": {"user_id": officer.email, "roles": list(officer.roles), "client": officer.client},
                     "query": {"text": body.get("question") or _canon(f), "skill": None}, "decisions": [], "flags": []})
        return {"events": shown, "count": len(shown)}

    @staticmethod
    def _matches(e: dict, f: dict) -> bool:
        space, decision, since, until = f.get("space"), f.get("decision"), f.get("from"), f.get("to")
        decisions = e.get("decisions", [])
        if space and not any(d.get("doc_id", "").startswith((space + "/", space + "-")) for d in decisions):
            return False
        if decision in ("allowed", "denied") and not any(d.get("allowed") is (decision == "allowed") for d in decisions):
            return False
        if since and e.get("ts", "") < since:
            return False
        return not (until and e.get("ts", "") > until)

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


def _canon(f: dict) -> str:
    import json
    return json.dumps(f, sort_keys=True)

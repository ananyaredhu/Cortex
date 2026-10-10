"""A connector backed by the shared fixtures. For development and as the reference for contract tests.

It is NOT a real or simulated platform: permissions are the token-overlap rule only.
Real connectors and simulators must pass the same contract tests with richer semantics.
"""
from connectors.base import (
    AccessDecision,
    AclEvidence,
    Change,
    ChangeBatch,
    Document,
    DocumentNotFound,
    PlatformIdentity,
    Source,
)
from fixtures.loader import State, can_see, persona_by_email, persona_by_id

POLICY_VERSION = "fixture-0.2"

_SOURCE_PREFIXES = {
    "slack": ("channel:",),
    "jira": ("role:", "group:jira:"),
    "confluence": ("group:confluence:",),
    "gdrive": ("group:gdrive:", "external:"),
}


class FixtureConnector:
    def __init__(self, source: Source, state: State | None = None):
        self.source = source
        self.state = state or State()
        self._log: list[Change] = []

    def _mine(self, token: str) -> bool:
        return token.startswith(_SOURCE_PREFIXES[self.source]) or token == "public:org"

    # -- helpers used by tests and the stub API -------------------------------------------------
    def advance(self, event_id: str) -> None:
        ev = self.state.advance(event_id)
        if ev["type"] == "upsert":
            doc = self.state.docs[ev["doc_id"]]
            if doc["source"] == self.source:
                self._log.append(Change("upsert", ev["doc_id"], ev["at"]))
        elif ev["type"] == "acl_change":
            # The fixture event takes tokens away from a persona. No document's ACL changes, so under
            # contract 0.2 this is one principal_change per token, not an acl_change per document.
            email = persona_by_id(self.state.data, ev["persona"])["email"]
            for token in ev["remove_tokens"]:
                if self._mine(token):
                    self._log.append(Change("principal_change", None, ev["at"], principal=f"user:{email}", token=token))

    def upsert(self, doc: dict) -> None:
        """Add or change a document of this source outside the scripted events, as Leak-CI plants hidden documents.
        `doc` has the fixture document shape (fixtures/company_a.json); the next `list_changes` reports it."""
        if doc["source"] != self.source:
            raise ValueError(f"{doc['doc_id']} is not a {self.source} document")
        self.state.docs[doc["doc_id"]] = doc
        self._log.append(Change("upsert", doc["doc_id"], self.state.data["now"]))

    def delete(self, doc_id: str) -> None:
        self._doc(doc_id)
        del self.state.docs[doc_id]
        self._log.append(Change("delete", doc_id, self.state.data["now"]))

    # -- Connector protocol ---------------------------------------------------------------------
    def resolve_identity(self, email: str) -> PlatformIdentity | None:
        p = persona_by_email(self.state.data, email)
        if p is None:
            return None
        toks = self.state.persona_tokens[p["id"]]
        return PlatformIdentity(self.source, f"{self.source}-{p['id']}", email, sorted(t for t in toks if self._mine(t)))

    def list_changes(self, cursor: str | None) -> ChangeBatch:
        """cursor=None is the full crawl: every document of this source as an upsert. Then the log from there on."""
        if cursor is None:
            now = self.state.data["now"]
            crawl = [Change("upsert", d["doc_id"], now) for d in self.state.docs.values() if d["source"] == self.source]
            return ChangeBatch(crawl, str(len(self._log)), False)
        return ChangeBatch(self._log[int(cursor):], str(len(self._log)), False)

    def _doc(self, doc_id: str) -> dict:
        d = self.state.docs.get(doc_id)
        if d is None or d["source"] != self.source:
            raise DocumentNotFound(doc_id)
        return d

    def fetch(self, doc_id: str) -> Document:
        d = self._doc(doc_id)
        a = d["acl"]
        return Document(
            doc_id=d["doc_id"], source=d["source"], kind=d["kind"], title=d["title"], url=d["url"],
            body=d["body"], parent_id=d["parent_id"], links=list(d["links"]), author=d["author"],
            created_at=d["created_at"], updated_at=d["updated_at"], version=d["version"],
            acl=AclEvidence(list(a["tokens"]), a["native"], a["snapshot_hash"], a["observed_at"]),
        )

    def check_access(self, identity: PlatformIdentity, doc_id: str) -> AccessDecision:
        """Fail closed: unknown docs and unknown identities are denied with the same shape."""
        now = self.state.data["now"]
        d = self.state.docs.get(doc_id)
        p = persona_by_email(self.state.data, identity.email)
        if d is None or p is None or d["source"] != self.source:
            return AccessDecision(False, [], now, "", POLICY_VERSION)
        toks = self.state.persona_tokens[p["id"]]
        if can_see(toks, d):
            hit = sorted(toks & set(d["acl"]["tokens"]))[0]
            return AccessDecision(True, [f"user:{p['email']}", hit], now, d["acl"]["snapshot_hash"], POLICY_VERSION)
        return AccessDecision(False, [], now, d["acl"]["snapshot_hash"], POLICY_VERSION)

    def version(self, doc_id: str) -> str:
        return self._doc(doc_id)["version"]

"""Test harness for the shared contract tests: the Slack connector, spoken to in fixture IDs.

The connector uses the workspace's own IDs. The contract tests use the fixture IDs. This wrapper translates
both ways through the seed manifest, which is what the contract means by "the contract tests translate
through it". `SeededSlack` runs against the in-memory fake; the same wrapper can sit on a real workspace.
"""
from dataclasses import replace

from connectors.base import AccessDecision, ChangeBatch, Document, DocumentNotFound, PlatformIdentity, Source
from connectors.slack.connector import SlackConnector
from connectors.slack.fake import FakeSlack, seed_company_a
from connectors.slack.manifest import SeedManifest
from fixtures.loader import load


class ManifestSlack:
    """A `Connector` over `inner` that takes and returns fixture IDs."""
    source: Source = "slack"

    def __init__(self, inner: SlackConnector, manifest: SeedManifest) -> None:
        self.inner = inner
        self.manifest = manifest

    def _real(self, doc_id: str) -> str:
        return self.manifest.real_doc(doc_id) or doc_id    # unknown ids go through unchanged and are not found

    def resolve_identity(self, email: str) -> PlatformIdentity | None:
        identity = self.inner.resolve_identity(email)
        if identity is None:
            return None
        return replace(identity, groups=sorted(self.manifest.fixture_token(t) for t in identity.groups))

    def list_changes(self, cursor: str | None) -> ChangeBatch:
        batch = self.inner.list_changes(cursor)
        changes = [replace(c, doc_id=(self.manifest.fixture_doc(c.doc_id) or c.doc_id) if c.doc_id else None,
                           token=self.manifest.fixture_token(c.token) if c.token else None) for c in batch.changes]
        return ChangeBatch(changes, batch.next_cursor, batch.has_more)

    def fetch(self, doc_id: str) -> Document:
        try:
            doc = self.inner.fetch(self._real(doc_id))
        except DocumentNotFound:
            raise DocumentNotFound(doc_id) from None
        acl = replace(doc.acl, tokens=sorted(self.manifest.fixture_token(t) for t in doc.acl.tokens))
        return replace(doc, doc_id=doc_id, parent_id=self.manifest.fixture_container(doc.parent_id or "") or doc.parent_id, acl=acl)

    def check_access(self, identity: PlatformIdentity, doc_id: str) -> AccessDecision:
        decision = self.inner.check_access(identity, self._real(doc_id))
        return replace(decision, proof_path=[self.manifest.fixture_token(t) for t in decision.proof_path])

    def version(self, doc_id: str) -> str:
        try:
            return self.inner.version(self._real(doc_id))
        except DocumentNotFound:
            raise DocumentNotFound(doc_id) from None

    def seed_version(self, doc: dict) -> str:
        """The version a seeded document has on this backend. Real platforms assign their own (here: the thread ts)."""
        return self.manifest.threads[doc["doc_id"]]


class SeededSlack(ManifestSlack):
    """The connector over a fresh fake workspace seeded with Company A. `slack` is exposed for tests."""

    def __init__(self) -> None:
        self.data = load()
        self.slack = FakeSlack()
        self.identities, manifest = seed_company_a(self.slack, self.data)
        super().__init__(SlackConnector(self.slack.client(), self.identities), manifest)

    def advance(self, event_id: str) -> None:
        """Apply a scripted fixture event the way an admin would in Slack."""
        event = next(e for e in self.data["events"] if e["id"] == event_id)
        if event["type"] != "acl_change":
            return   # the scripted edit is Confluence's
        email = next(p["email"] for p in self.data["personas"] if p["id"] == event["persona"])
        user = self.slack.user_id(self.identities.platform_account("slack", email))
        for token in event["remove_tokens"]:
            kind, _, channel = self.manifest.real_token(token).partition(":")
            if kind == "channel" and channel in self.slack.channels:
                self.slack.leave(channel, user)

    # No `restrict_document`: Slack cannot restrict one thread. No `revoke_container`: a channel has no grant
    # to take away other than its own membership. Both contract tests are expected failures for Slack; making a
    # channel private and removing a member are covered in connectors/slack/tests/.

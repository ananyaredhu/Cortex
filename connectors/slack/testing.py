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
        # Channels the manifest does not map (#general and the like on a real workspace) hold no seeded documents
        # and do not exist in the fixture world: their tokens are left out. That only narrows access.
        groups = [self.manifest.fixture_token(t) for t in identity.groups]
        return replace(identity, groups=sorted(t for t in groups if not t.startswith("channel:") or t[8:] in self.manifest.channels))

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


HAND_ENV = "CONTRACT_REAL_HAND"   # set to 1 to let a test wait for a change made by hand in Slack
HAND_TIMEOUT = 600.0               # seconds to wait for it


class RealSlack(ManifestSlack):
    """The connector on the real workspace, through the seed manifest. Opt-in: see connectors/tests/contract.

    The bot token is read-only, so a scripted permission change has to be made by a person in Slack. `advance`
    says what to do and waits until the live `check_access` shows it; without CONTRACT_REAL_HAND=1 (and pytest
    run with `-s`, so the instruction is visible) it is an expected failure instead. Prints fixture IDs,
    persona names and channel names only.
    """
    why_no_permission_hooks = "the bot token is read-only and Slack has no per-thread restriction or channel grant"

    def __init__(self) -> None:
        from connectors.env import load_dotenv
        load_dotenv()
        self.data = load()
        super().__init__(SlackConnector.from_env(), SeedManifest.load())

    def advance(self, event_id: str) -> None:
        import os
        import time

        import pytest

        event = next(e for e in self.data["events"] if e["id"] == event_id)
        if event["type"] != "acl_change":
            return   # the scripted edit is Confluence's
        if os.environ.get(HAND_ENV) != "1":
            pytest.xfail(f"event {event_id} must be made by hand in Slack: run with {HAND_ENV}=1 and pytest -s")
        persona = next(p for p in self.data["personas"] if p["id"] == event["persona"])
        identity = self.resolve_identity(persona["email"])
        assert identity is not None, f"{persona['id']} has no Slack identity"
        for token in event["remove_tokens"]:
            channel = token.split(":", 1)[1]
            doc_id = next(d["doc_id"] for d in self.data["documents"] if d["source"] == "slack" and token in d["acl"]["tokens"])
            name = self.fetch(doc_id).title.split(" ", 1)[0]
            print(f"\n>>> BY HAND in Slack: remove {persona['id'].title()} from {name} (fixture {channel}), "
                  f"signed in as an account that can, e.g. outsider. Waiting up to {HAND_TIMEOUT:.0f} s...", flush=True)
            deadline = time.monotonic() + HAND_TIMEOUT
            while self.check_access(identity, doc_id).allowed:
                if time.monotonic() > deadline:
                    pytest.fail(f"{persona['id']} still reads {doc_id} after {HAND_TIMEOUT:.0f} s")
                time.sleep(3)
            print(f">>> seen: {persona['id']} no longer reads {doc_id}", flush=True)

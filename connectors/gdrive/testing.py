"""Test harness for the shared contract tests: the Drive connector over the in-memory fake, seeded with Company A.

`SeededDrive` uses the fixture file and folder IDs, so no translation is needed. A real Drive assigns its own IDs:
`ManifestDrive` translates both ways through the seed manifest, which is what the contract means by "the contract
tests translate through it". `RenamedSeededDrive` proves that path on the fake: its IDs differ from the fixtures and
its manifest is built by `build_manifest`, as on real Drive.
"""
from dataclasses import replace

from connectors.base import AccessDecision, ChangeBatch, Document, DocumentNotFound, PlatformIdentity, Source
from connectors.gdrive.build_manifest import build
from connectors.gdrive.connector import DriveConnector
from connectors.gdrive.fake import GROUP_ADDRESS, FakeDrive, seed_company_a
from connectors.gdrive.manifest import SeedManifest
from fixtures.loader import load


class SeededDrive(DriveConnector):
    """`drive` is exposed for tests. `rename` gives the fake its own IDs (see `seed_company_a`)."""

    def __init__(self, *, rename=None, **kwargs) -> None:
        self.data = load()
        self.drive = FakeDrive()
        sessions, self.identities, self.config = seed_company_a(self.drive, self.data, rename=rename)
        super().__init__(sessions, self.identities, self.config, **kwargs)

    def advance(self, event_id: str) -> None:
        """Neither scripted event touches Drive."""

    def seed_version(self, doc: dict) -> str:
        """Drive assigns its own version counter, so a seeded file's version is the counter it started at."""
        return "1"

    def _address(self, token: str) -> tuple[str, str]:
        """(permission type, address) for the principal `token` names."""
        kind, _, name = token.partition(":")
        if kind in ("user", "external"):
            return "user", self.identities.platform_account("gdrive", name)
        return "group", GROUP_ADDRESS

    def restrict_document(self, doc_id: str, token: str) -> None:
        """Restrict the file to the one principal `token` names, the way an owner would on Drive: move it to an
        unshared folder (so it stops inheriting) and share it with that principal alone."""
        file = self.drive.files[doc_id.split(":", 1)[1]]
        kind, address = self._address(token)
        private = self.drive.add_folder(f"restricted-{file['id']}", "restricted", owner=file["owner"])
        self.config.root_folders.append(private)    # still Company A's, so still in scope
        file["parents"] = [private]
        file["permissions"] = []
        self.drive.share(file["id"], address, kind=kind)

    def revoke_container(self, container_id: str, token: str) -> None:
        """Stop sharing the folder with the principal `token` names."""
        self.drive.unshare(container_id.split(":", 1)[1], self._address(token)[1])


class ManifestDrive:
    """A `Connector` over `inner` that takes and returns fixture IDs. Drive tokens name people and groups, not
    files, so tokens and proof paths pass through unchanged."""
    source: Source = "gdrive"

    def __init__(self, inner: DriveConnector, manifest: SeedManifest) -> None:
        self.inner = inner
        self.manifest = manifest

    def _real(self, doc_id: str) -> str:
        return self.manifest.real_doc(doc_id) or doc_id    # unknown ids go through unchanged and are not found

    def resolve_identity(self, email: str) -> PlatformIdentity | None:
        return self.inner.resolve_identity(email)

    def list_changes(self, cursor: str | None) -> ChangeBatch:
        batch = self.inner.list_changes(cursor)
        changes = [replace(c, doc_id=self.manifest.fixture_doc(c.doc_id) or c.doc_id) if c.doc_id else c for c in batch.changes]
        return ChangeBatch(changes, batch.next_cursor, batch.has_more)

    def fetch(self, doc_id: str) -> Document:
        try:
            doc = self.inner.fetch(self._real(doc_id))
        except DocumentNotFound:
            raise DocumentNotFound(doc_id) from None
        return replace(doc, doc_id=doc_id, parent_id=self.manifest.fixture_container(doc.parent_id or "") or doc.parent_id)

    def check_access(self, identity: PlatformIdentity, doc_id: str) -> AccessDecision:
        return self.inner.check_access(identity, self._real(doc_id))

    def version(self, doc_id: str) -> str:
        try:
            return self.inner.version(self._real(doc_id))
        except DocumentNotFound:
            raise DocumentNotFound(doc_id) from None

    def seed_version(self, doc: dict) -> str:
        """The version a seeded file has on this backend: its modified time when the manifest was built."""
        return self.manifest.versions[doc["doc_id"]]


class RenamedSeededDrive(ManifestDrive):
    """The fake with its own IDs, spoken to in fixture IDs through a manifest built by `build_manifest`."""

    def __init__(self) -> None:
        drive = SeededDrive(rename=lambda native_id: f"1Real{native_id.replace('-', '')}")
        manifest, problems = build(drive, drive.data)
        assert not problems, problems
        super().__init__(drive, manifest)
        self.drive = drive.drive

    def advance(self, event_id: str) -> None:
        self.inner.advance(event_id)

    def restrict_document(self, doc_id: str, token: str) -> None:
        self.inner.restrict_document(self._real(doc_id), token)

    def revoke_container(self, container_id: str, token: str) -> None:
        self.inner.revoke_container(self.manifest.real_container(container_id) or container_id, token)


class RealDrive(ManifestDrive):
    """The connector on real Drive (the signed-in accounts), through the seed manifest. Opt-in: see
    connectors/tests/contract. Neither scripted event touches Drive. Permission changes would need write
    scopes, which the connector does not ask for, so there are no permission hooks."""
    why_no_permission_hooks = "the Drive scopes are read-only, so a test cannot change sharing"
    # Personal accounts have no Google Groups: files are shared with payments-eng's members one by one, and the
    # connector writes that as `group:gdrive:payments-eng` without the members' own tokens. Same readers, other tokens.
    compare_acl_by_readers = True

    def __init__(self) -> None:
        from connectors.env import load_dotenv
        load_dotenv()
        super().__init__(DriveConnector.from_env(), SeedManifest.load())

    def advance(self, event_id: str) -> None:
        """Neither scripted event touches Drive."""

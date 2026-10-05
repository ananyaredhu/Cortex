"""Test harness for the shared contract tests: the Drive connector over the in-memory fake, seeded with Company A.

The fake uses the fixture file and folder IDs, so no translation is needed here. A real Drive assigns its own IDs
and will need a seed manifest like Slack's.
"""
from connectors.gdrive.connector import DriveConnector
from connectors.gdrive.fake import GROUP_ADDRESS, FakeDrive, seed_company_a
from fixtures.loader import load


class SeededDrive(DriveConnector):
    """`drive` is exposed for tests."""

    def __init__(self, **kwargs) -> None:
        self.data = load()
        self.drive = FakeDrive()
        sessions, self.identities, self.config = seed_company_a(self.drive, self.data)
        super().__init__(sessions, self.identities, self.config, **kwargs)

    def advance(self, event_id: str) -> None:
        """Neither scripted event touches Drive."""

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

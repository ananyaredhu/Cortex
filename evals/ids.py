"""Fixture document IDs <-> the IDs the real Slack workspace and Google Drive assigned.

The golden cases name documents by fixture ID (`slack:C_DBMIG/thread-1`, `gdrive:postmortem-pay-outage`). The stub
API answers in those IDs; the real system cites what the platforms assigned (`slack:C07ABC123/1791619200.000100`).
Workstream A's seed manifests map between them (connector-interface.md, "Document granularity and IDs"):
`connectors/slack/seed-manifest.local.json` and `connectors/gdrive/seed-manifest.local.json`, both gitignored and
built with `python -m connectors.slack.build_manifest` and `python -m connectors.gdrive.build_manifest`.
Confluence and Jira are simulated with the fixture IDs, so they pass through unchanged.

Fail closed: a case that names a Slack or Drive document the manifests do not map cannot be checked (a real-ID
citation of it would never match `must_not_cite`), so `require` refuses to run such cases.
"""
from connectors.gdrive.manifest import SeedManifest as DriveManifest
from connectors.slack.manifest import SeedManifest as SlackManifest

MAPPED_SOURCES = ("slack", "gdrive")
_RULE_SETS = ("before_event", "after")


class UnmappedDocuments(Exception):
    """The golden cases name real-platform documents that the seed manifests do not map."""


class SeedIds:
    """Translates document IDs between fixture and real. `fixture()` is the identity, for the stub API."""

    def __init__(self, slack: SlackManifest | None = None, gdrive: DriveManifest | None = None) -> None:
        self._slack = slack
        self._gdrive = gdrive

    @classmethod
    def fixture(cls) -> "SeedIds":
        return cls()

    @classmethod
    def from_manifests(cls) -> "SeedIds":
        """Both local manifests (paths overridable with SLACK_SEED_MANIFEST_PATH and GDRIVE_SEED_MANIFEST_PATH)."""
        return cls(SlackManifest.load(), DriveManifest.load())

    def _manifest(self, doc_id: str):
        source = doc_id.split(":", 1)[0]
        return {"slack": self._slack, "gdrive": self._gdrive}.get(source)

    def to_fixture(self, doc_id: str) -> str:
        manifest = self._manifest(doc_id)
        return (manifest.fixture_doc(doc_id) if manifest else None) or doc_id

    def to_real(self, doc_id: str) -> str:
        manifest = self._manifest(doc_id)
        return (manifest.real_doc(doc_id) if manifest else None) or doc_id

    def unmapped(self, cases: list[dict]) -> list[str]:
        """Slack and Drive fixture documents named by `cases` that have no real ID. Always empty for `fixture()`."""
        if self._slack is None and self._gdrive is None:
            return []
        named = {doc_id for case in cases for rules in [case, *(case.get(k, {}) for k in _RULE_SETS)]
                 for key in ("must_cite", "must_not_cite") for doc_id in rules.get(key, [])}
        return sorted(d for d in named if d.split(":", 1)[0] in MAPPED_SOURCES and self.to_real(d) == d)

    def require(self, cases: list[dict]) -> None:
        missing = self.unmapped(cases)
        if missing:
            raise UnmappedDocuments("no real ID for " + ", ".join(missing) + ": rebuild the seed manifests "
                                    "(python -m connectors.slack.build_manifest, python -m connectors.gdrive.build_manifest)")

    def response_in_fixture_ids(self, resp: dict) -> dict:
        """`resp` (an /v1/ask answer) with every cited doc_id translated to its fixture ID."""
        out = dict(resp)
        if "citations" in resp:
            out["citations"] = [{**c, "doc_id": self.to_fixture(c["doc_id"])} for c in resp["citations"]]
        if "claims" in resp:
            out["claims"] = [{**c, "citations": [self.to_fixture(d) for d in c.get("citations", [])]} for c in resp["claims"]]
        return out

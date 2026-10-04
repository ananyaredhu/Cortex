"""Test harness: a seeded simulator behind its own HTTP API, plus the hooks the shared contract tests use.

Everything goes through HTTP (FastAPI's in-process TestClient), so the contract tests cover the REST
layer and the admin endpoints as well as the permission model.
"""
from fastapi.testclient import TestClient

from fixtures.loader import load
from simulators.confluence.app import create_app
from simulators.confluence.connector import ConfluenceConnector
from simulators.confluence.model import ConfluenceSim
from simulators.confluence.seed import seed_company_a
from simulators.confluence.tokens import principal_from_token


class SeededConfluence(ConfluenceConnector):
    """Connector over a fresh simulator seeded with Company A. `sim` and `http` are exposed for tests."""

    def __init__(self) -> None:
        self.data = load()
        self.sim = ConfluenceSim()
        seed_company_a(self.sim, self.data)
        self.http = TestClient(create_app(self.sim))
        super().__init__(self.http)

    def advance(self, event_id: str) -> None:
        """Apply a scripted fixture event through the admin API."""
        event = next(e for e in self.data["events"] if e["id"] == event_id)
        if event["type"] == "upsert" and event["doc_id"].startswith("confluence:"):
            page_id = event["doc_id"].split("/", 1)[1]
            patch = {k: event["patch"][k] for k in ("title", "body", "version", "updated_at") if k in event["patch"]}
            self.http.put(f"/sim/admin/pages/{page_id}", json=patch).raise_for_status()
        elif event["type"] == "acl_change":
            email = next(p["email"] for p in self.data["personas"] if p["id"] == event["persona"])
            for token in event["remove_tokens"]:
                kind, name = principal_from_token(token) or (None, None)
                if kind == "group":
                    self.http.delete(f"/sim/admin/groups/{name}/members/{email}").raise_for_status()

    def restrict_document(self, doc_id: str, token: str) -> None:
        """Restrict the page itself to the one principal `token` names (document-level ACL change)."""
        page_id = doc_id.split("/", 1)[1]
        kind, name = principal_from_token(token) or (None, None)
        body = {"groups": [name] if kind == "group" else [], "users": [name] if kind == "user" else []}
        self.http.put(f"/sim/admin/pages/{page_id}/restrictions", json=body).raise_for_status()

    def revoke_container(self, container_id: str, token: str) -> None:
        """Take `token` off the space's view permission (container-level revocation)."""
        key = container_id.split(":", 1)[1]
        view = self.sim.spaces[key].view
        kind, name = principal_from_token(token) or (None, None)
        groups = sorted(view.groups - ({name} if kind == "group" else set()))
        users = sorted(view.users - ({name} if kind == "user" else set()))
        self.http.put(f"/sim/admin/spaces/{key}/permissions", json={"groups": groups, "users": users}).raise_for_status()

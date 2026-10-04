"""Test harness: a seeded simulator behind its own HTTP API, plus the hooks the shared contract tests use.

Everything goes through HTTP (FastAPI's in-process TestClient), so the contract tests cover the REST
layer and the admin endpoints as well as the permission model.
"""
from fastapi.testclient import TestClient

from fixtures.loader import load
from simulators.jira.app import create_app
from simulators.jira.connector import JiraConnector
from simulators.jira.model import JiraSim
from simulators.jira.seed import seed_company_a
from simulators.jira.tokens import principal_from_token


class SeededJira(JiraConnector):
    """Connector over a fresh simulator seeded with Company A. `sim` and `http` are exposed for tests."""

    def __init__(self) -> None:
        self.data = load()
        self.sim = JiraSim()
        seed_company_a(self.sim, self.data)
        self.http = TestClient(create_app(self.sim))
        super().__init__(self.http)

    def advance(self, event_id: str) -> None:
        """Apply a scripted fixture event through the admin API."""
        event = next(e for e in self.data["events"] if e["id"] == event_id)
        if event["type"] == "upsert" and event["doc_id"].startswith("jira:"):
            key = event["doc_id"].split(":", 1)[1]
            names = {"title": "summary", "body": "description", "version": "version", "updated_at": "updated_at"}
            patch = {names[k]: v for k, v in event["patch"].items() if k in names}
            self.http.put(f"/sim/admin/issues/{key}", json=patch).raise_for_status()
        elif event["type"] == "acl_change":
            email = next(p["email"] for p in self.data["personas"] if p["id"] == event["persona"])
            for token in event["remove_tokens"]:
                kind, name, project = principal_from_token(token) or (None, None, None)
                if kind == "group":
                    self.http.delete(f"/sim/admin/groups/{name}/members/{email}").raise_for_status()
                elif kind == "role" and project in self.sim.projects:
                    self.http.delete(f"/sim/admin/projects/{project}/roles/{name}/users/{email}").raise_for_status()

    def revoke_container(self, container_id: str, token: str) -> None:
        """Take `token` off the project's Browse permission (container-level revocation)."""
        key = container_id.split(":", 1)[1]
        browse = self.sim.projects[key].browse
        kind, name, _ = principal_from_token(token) or (None, None, None)
        body = {
            "roles": sorted(browse.roles - ({name} if kind == "role" else set())),
            "groups": sorted(browse.groups - ({name} if kind == "group" else set())),
            "users": sorted(browse.users - ({name} if kind == "user" else set())),
        }
        self.http.put(f"/sim/admin/projects/{key}/permissions", json=body).raise_for_status()

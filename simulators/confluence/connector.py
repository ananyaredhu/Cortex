"""Connector for the Confluence simulator (docs/02-contracts/connector-interface.md).

It talks to the simulator only over HTTP, the way a connector for a real Confluence tenant would, so
the REST shapes get exercised and the simulator can run as its own service.

`check_access` asks the simulator live and fails closed: any error, timeout or surprise is a deny.
"""
from datetime import datetime, timezone

import httpx

from connectors.base import AccessDecision, AclEvidence, ChangeBatch, CursorExpired, Document, DocumentNotFound, PlatformIdentity, Source
from simulators.common import to_change
from simulators.confluence.model import valid_id
from simulators.confluence.tokens import group_token

API = "/wiki/rest/api"
POLICY_VERSION = "confluence-sim-0.1"

__all__ = ["ConfluenceConnector", "DocumentNotFound"]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(doc_id: str) -> tuple[str, str] | None:
    """"confluence:<space>/<page id>" -> (space, page id). None if it is not a well-formed Confluence doc id."""
    prefix, _, rest = doc_id.partition(":")
    space, _, page_id = rest.partition("/")
    if prefix != "confluence" or not valid_id(space) or not valid_id(page_id):
        return None
    return space, page_id


class ConfluenceConnector:
    source: Source = "confluence"

    def __init__(self, client: httpx.Client, *, policy_version: str = POLICY_VERSION, page_size: int = 500) -> None:
        """`client` must have the simulator as its base URL and a short timeout (check_access is on the query path)."""
        self._client = client
        self._policy_version = policy_version
        self._page_size = page_size

    @classmethod
    def from_url(cls, base_url: str, *, timeout: float = 2.0) -> "ConfluenceConnector":
        return cls(httpx.Client(base_url=base_url, timeout=timeout))

    # -- Connector protocol ---------------------------------------------------------------------
    def resolve_identity(self, email: str) -> PlatformIdentity | None:
        """None when the email has no Confluence account, or when the lookup fails (fail closed)."""
        try:
            found = self._client.get(f"{API}/user", params={"email": email})
            if found.status_code != 200:
                return None
            account_id = found.json()["accountId"]
            groups = self._client.get(f"{API}/user/memberof", params={"accountId": account_id})
            groups.raise_for_status()
            tokens = sorted(group_token(g["name"]) for g in groups.json()["results"])
        except Exception:   # fail closed on anything: network, timeout, malformed response
            return None
        return PlatformIdentity(self.source, account_id, email, tokens)

    def list_changes(self, cursor: str | None) -> ChangeBatch:
        """cursor=None crawls every page as an upsert; afterwards it returns incremental changes."""
        params: dict[str, str | int] = {"limit": self._page_size}
        if cursor is not None:
            params["cursor"] = cursor
        response = self._client.get("/sim/changes", params=params)
        if response.status_code == 410:
            raise CursorExpired(cursor)
        response.raise_for_status()
        data = response.json()
        return ChangeBatch([to_change(c) for c in data["changes"]], data["next_cursor"], data["has_more"])

    def fetch(self, doc_id: str) -> Document:
        page = self._page(doc_id)
        extra = page["_simulator"]
        acl = extra["acl"]
        space = page["space"]["key"]
        return Document(
            doc_id=doc_id,
            source=self.source,
            kind="page",
            title=page["title"],
            url=page["_links"]["base"] + page["_links"]["webui"],
            body=page["body"]["storage"]["value"],
            parent_id=f"confluence:{space}",
            links=list(extra["links"]),
            author=page["history"]["createdBy"]["email"] or None,
            created_at=page["history"]["createdDate"],
            updated_at=page["version"]["when"],
            version=extra["version"],
            acl=AclEvidence(list(acl["tokens"]), acl["native"], acl["snapshotHash"], _now()),
        )

    def check_access(self, identity: PlatformIdentity, doc_id: str) -> AccessDecision:
        """Live answer from the simulator. Group membership is evaluated there, now: `identity.groups` is not trusted."""
        deny = AccessDecision(False, [], _now(), "", self._policy_version)
        try:
            parsed = _parse(doc_id)
            if parsed is None or identity.source != self.source:
                return deny
            space, page_id = parsed
            response = self._client.post(
                f"{API}/content/{page_id}/permission/check",
                json={"subject": {"type": "user", "identifier": identity.platform_user_id}, "operation": "read"},
            )
            if response.status_code != 200:
                return deny
            data = response.json()
            extra = data.get("_simulator", {})
            if extra.get("spaceKey") != space:
                return deny   # unknown page, or a doc id that names the wrong space
            snapshot = str(extra.get("aclSnapshotHash", ""))
            if data.get("hasPermission") is True and extra.get("grantPath"):
                return AccessDecision(True, list(extra["grantPath"]), _now(), snapshot, self._policy_version)
            return AccessDecision(False, [], _now(), snapshot, self._policy_version)
        except Exception:   # fail closed on anything: network, timeout, malformed response
            return deny

    def version(self, doc_id: str) -> str:
        return self._page(doc_id)["_simulator"]["version"]

    # -- helpers --------------------------------------------------------------------------------
    def _page(self, doc_id: str) -> dict:
        parsed = _parse(doc_id)
        if parsed is None:
            raise DocumentNotFound(doc_id)
        space, page_id = parsed
        response = self._client.get(f"{API}/content/{page_id}")
        if response.status_code == 404:
            raise DocumentNotFound(doc_id)
        response.raise_for_status()
        page = response.json()
        if page["space"]["key"] != space:
            raise DocumentNotFound(doc_id)
        return page

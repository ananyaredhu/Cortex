"""Connector for the Jira simulator (docs/02-contracts/connector-interface.md).

It talks to the simulator only over HTTP, the way a connector for a real Jira site would.
`check_access` asks the simulator live and fails closed: any error, timeout or surprise is a deny.
"""
import httpx

from connectors.base import (
    AccessDecision,
    AclEvidence,
    ChangeBatch,
    CursorExpired,
    Document,
    DocumentNotFound,
    PlatformIdentity,
    Source,
    TransitionRefused,
)
from simulators.common import to_change, utc_now
from simulators.jira.model import valid_issue_key

API = "/rest/api/2"
POLICY_VERSION = "jira-sim-0.1"


def _parse(doc_id: str) -> str | None:
    """"jira:<ISSUE-KEY>" -> issue key. None if it is not a well-formed Jira doc id."""
    prefix, _, key = doc_id.partition(":")
    return key if prefix == "jira" and valid_issue_key(key) else None


def _fields(fields: dict) -> dict[str, str]:
    """`Document.fields` (connector-interface 0.4): what Jira holds as fields, for code to compare claims with."""
    out = {}
    if (fields.get("status") or {}).get("name"):
        out["status"] = fields["status"]["name"]
    if (fields.get("assignee") or {}).get("displayName"):
        out["assignee"] = fields["assignee"]["displayName"]
    # 0.5: whose the issue is by account, and its deadline and priority. Absent, never empty, when the issue has none.
    if (fields.get("assignee") or {}).get("accountId") and fields["assignee"].get("emailAddress"):
        out["assignee_email"] = fields["assignee"]["emailAddress"].lower()
    if fields.get("duedate"):
        out["due"] = fields["duedate"]
    if (fields.get("priority") or {}).get("name"):
        out["priority"] = fields["priority"]["name"]
    return out


def _body(fields: dict) -> str:
    """One document per issue: description, then status and assignee, then comments (contract: "one issue, including its
    comments"). `Status: <name>` and `Assignee: <display name>` are the lines Team focus reads (brain/projects/team.py)."""
    description = fields.get("description") or ""
    parts = [description]
    held = _fields(fields)
    for line in (f"{label}: {held[name]}" for label, name in (("Status", "status"), ("Assignee", "assignee"), ("Due", "due"),
                                                               ("Priority", "priority")) if held.get(name)):
        if line not in description:          # a description that already says it (the seeded issues) is not told twice
            parts.append(line)
    for comment in fields["comment"]["comments"]:
        author = (comment.get("author") or {}).get("emailAddress") or "unknown"
        parts.append(f"Comment by {author} ({comment['created']}): {comment['body']}")
    return "\n\n".join(p for p in parts if p)


class JiraConnector:
    source: Source = "jira"

    def __init__(self, client: httpx.Client, *, policy_version: str = POLICY_VERSION, page_size: int = 500) -> None:
        """`client` must have the simulator as its base URL and a short timeout (check_access is on the query path)."""
        self._client = client
        self._policy_version = policy_version
        self._page_size = page_size

    @classmethod
    def from_url(cls, base_url: str, *, timeout: float = 2.0) -> "JiraConnector":
        return cls(httpx.Client(base_url=base_url, timeout=timeout))

    # -- Connector protocol ---------------------------------------------------------------------
    def resolve_identity(self, email: str) -> PlatformIdentity | None:
        """None when the email has no Jira account, or when the lookup fails (fail closed)."""
        try:
            found = self._client.get(f"{API}/user/search", params={"query": email})
            found.raise_for_status()
            account_id = next((u["accountId"] for u in found.json() if u["emailAddress"].lower() == email.lower()), None)
            if account_id is None:
                return None
            held = self._client.get(f"/sim/users/{account_id}/tokens")
            held.raise_for_status()
            tokens = sorted(str(t) for t in held.json())
        except Exception:   # fail closed on anything: network, timeout, malformed response
            return None
        return PlatformIdentity(self.source, account_id, email, tokens)

    def list_changes(self, cursor: str | None) -> ChangeBatch:
        """cursor=None crawls every issue as an upsert; afterwards it returns incremental changes."""
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
        return self._document(doc_id, self._issue(doc_id))

    def transition(self, identity: PlatformIdentity, doc_id: str, status: str) -> Document:
        """Move the issue to `status` as `identity` (connector-interface 0.5, "Writing"). The simulator applies its own
        permission and workflow; this connector decides nothing and retries nothing."""
        key = _parse(doc_id)
        if key is None or identity.source != self.source:
            raise DocumentNotFound(doc_id)
        acting = {"X-Sim-User": identity.email}
        offered = self._client.get(f"{API}/issue/{key}/transitions", headers=acting)
        if offered.status_code == 404:
            raise DocumentNotFound(doc_id)
        offered.raise_for_status()
        to = next((t["id"] for t in offered.json()["transitions"] if t["to"]["name"].lower() == status.strip().lower()), None)
        if to is None:
            raise TransitionRefused(doc_id)
        moved = self._client.post(f"{API}/issue/{key}/transitions", json={"transition": {"id": to}}, headers=acting)
        if moved.status_code == 404:
            raise DocumentNotFound(doc_id)
        if moved.status_code == 400:
            raise TransitionRefused(doc_id)
        moved.raise_for_status()
        return self._document(doc_id, moved.json())

    def _document(self, doc_id: str, issue: dict) -> Document:
        fields, extra = issue["fields"], issue["_simulator"]
        acl = extra["acl"]
        return Document(
            doc_id=doc_id,
            source=self.source,
            kind="issue",
            title=fields["summary"],
            url=extra["browseUrl"],
            body=_body(fields),
            parent_id=f"jira:{fields['project']['key']}",
            links=list(extra["links"]),
            author=(fields.get("reporter") or {}).get("emailAddress") or None,
            created_at=fields["created"],
            updated_at=fields["updated"],
            version=extra["version"],
            acl=AclEvidence(list(acl["tokens"]), acl["native"], acl["snapshotHash"], utc_now()),
            fields=_fields(fields),
        )

    def check_access(self, identity: PlatformIdentity, doc_id: str) -> AccessDecision:
        """Live answer from the simulator. Roles and groups are evaluated there, now: `identity.groups` is not trusted."""
        deny = AccessDecision(False, [], utc_now(), "", self._policy_version)
        try:
            key = _parse(doc_id)
            if key is None or identity.source != self.source:
                return deny
            response = self._client.post(f"{API}/permissions/check", json={
                "accountId": identity.platform_user_id,
                "projectPermissions": [{"permissions": ["BROWSE_PROJECTS"], "issues": [key]}],
            })
            if response.status_code != 200:
                return deny
            data = response.json()
            detail = data.get("_simulator", {}).get("issues", {}).get(key)
            if detail is None:
                return deny   # the issue does not exist
            snapshot = str(detail.get("aclSnapshotHash", ""))
            granted = data["projectPermissions"][0]["issues"]
            if isinstance(granted, list) and key in granted and detail.get("grantPath"):
                return AccessDecision(True, list(detail["grantPath"]), utc_now(), snapshot, self._policy_version)
            return AccessDecision(False, [], utc_now(), snapshot, self._policy_version)
        except Exception:   # fail closed on anything: network, timeout, malformed response
            return deny

    def version(self, doc_id: str) -> str:
        return self._issue(doc_id)["_simulator"]["version"]

    # -- helpers --------------------------------------------------------------------------------
    def _issue(self, doc_id: str) -> dict:
        key = _parse(doc_id)
        if key is None:
            raise DocumentNotFound(doc_id)
        response = self._client.get(f"{API}/issue/{key}")
        if response.status_code == 404:
            raise DocumentNotFound(doc_id)
        response.raise_for_status()
        return response.json()

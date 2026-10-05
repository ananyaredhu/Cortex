"""An in-memory stand-in for the Drive v3 API and Google's token endpoint, for tests.

It models what matters for permissions on personal accounts: every request acts as one signed-in account, a file
is invisible (404) to an account it is not shared with, sharing is inherited from parent folders, and the sharing
list is only shown to people who can share the file (owner or editor).

It is a test double, not a simulator: no change feed, no shared drives, no uploads.
"""
import re
import time
from collections import Counter

import httpx

from connectors.gdrive.client import DriveSession
from connectors.gdrive.config import DriveConfig, Group
from connectors.gdrive.connector import FOLDER, GOOGLE_DOC
from connectors.identity_map import IdentityMap

API = "https://drive.test/drive/v3/"
TOKEN = "https://drive.test/token"
_PARENT_QUERY = re.compile(r"'([^']+)' in parents")


def _error(status: int, reason: str) -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": status, "errors": [{"reason": reason}]}})


class FakeDrive:
    def __init__(self) -> None:
        self.files: dict[str, dict] = {}
        self.group_members: dict[str, set[str]] = {}    # Google Group address -> member account addresses
        self.calls: Counter = Counter()
        self.throttle = 0             # answer this many API requests with HTTP 429 first
        self.expire_tokens = 0        # answer this many API requests with HTTP 401 first
        self.down = False
        self.delay = 0.0
        self._ids = 0

    # -- building and changing the drive ------------------------------------------------------------
    def add(self, file_id: str, name: str, *, owner: str, parent: str | None = None, mime: str = GOOGLE_DOC, content: str = "",
            modified: str = "2026-10-10T08:00:00Z") -> str:
        self.files[file_id] = {"id": file_id, "name": name, "mimeType": mime, "parents": [parent] if parent else [], "owner": owner,
                               "permissions": [], "content": content, "modifiedTime": modified, "createdTime": "2026-10-10T00:00:00Z",
                               "trashed": False}
        return file_id

    def add_folder(self, folder_id: str, name: str, *, owner: str, parent: str | None = None) -> str:
        return self.add(folder_id, name, owner=owner, parent=parent, mime=FOLDER)

    def share(self, file_id: str, address: str | None = None, *, role: str = "reader", kind: str = "user") -> None:
        self._ids += 1
        self.files[file_id]["permissions"].append({"id": f"perm{self._ids}", "type": kind, "role": role, "emailAddress": address})

    def unshare(self, file_id: str, address: str) -> None:
        self.files[file_id]["permissions"] = [p for p in self.files[file_id]["permissions"] if p.get("emailAddress") != address]

    def edit(self, file_id: str, content: str, modified: str) -> None:
        self.files[file_id].update(content=content, modifiedTime=modified)

    def session(self, account: str, **kwargs) -> DriveSession:
        """A session signed in as `account`."""
        http = httpx.Client(transport=httpx.MockTransport(self._handle))
        return DriveSession(http, refresh_token=f"rt:{account}", client_id="fake-client", client_secret="fake-secret",
                            api_url=API, token_url=TOKEN, sleep=kwargs.pop("sleep", lambda seconds: None), **kwargs)

    # -- rules ----------------------------------------------------------------------------------------
    def _effective(self, file: dict) -> list[dict]:
        """The file's sharing list as Drive reports it: the owner, its own grants, and what its folders pass down."""
        out = [{"id": "owner", "type": "user", "role": "owner", "emailAddress": file["owner"]}]
        node, depth = file, 0
        while node is not None and depth < 50:
            out += node["permissions"]
            node = self.files.get(node["parents"][0]) if node["parents"] else None
            depth += 1
        return out

    def _role(self, actor: str, file: dict) -> str | None:
        roles = [p["role"] for p in self._effective(file)
                 if p["type"] == "anyone" or (p["type"] == "user" and p["emailAddress"] == actor)
                 or (p["type"] == "group" and actor in self.group_members.get(p["emailAddress"], set()))]
        return next((r for r in ("owner", "writer", "commenter", "reader") if r in roles), None)

    def _visible(self, actor: str, file_id: str) -> dict | None:
        file = self.files.get(file_id)
        return file if file is not None and self._role(actor, file) else None

    @staticmethod
    def _json(file: dict) -> dict:
        return {"id": file["id"], "name": file["name"], "mimeType": file["mimeType"], "parents": list(file["parents"]),
                "trashed": file["trashed"], "modifiedTime": file["modifiedTime"], "createdTime": file["createdTime"],
                "webViewLink": f"https://drive.test/file/d/{file['id']}/view", "owners": [{"emailAddress": file["owner"]}]}

    # -- the API --------------------------------------------------------------------------------------
    def _handle(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("fake Drive is down", request=request)
        if self.delay:
            time.sleep(self.delay)
        if str(request.url).startswith(TOKEN):
            refresh = httpx.QueryParams(request.content.decode()).get("refresh_token", "")
            self.calls["token"] += 1
            if not refresh.startswith("rt:"):
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": f"at:{refresh[3:]}", "expires_in": 3600})
        path = request.url.path.removeprefix("/drive/v3/")
        self.calls[path.rpartition("/")[2] if path.count("/") >= 2 else ("list" if path == "files" else "get")] += 1
        if self.throttle > 0:
            self.throttle -= 1
            limited = _error(429, "rateLimitExceeded")
            limited.headers["Retry-After"] = "2"
            return limited
        if self.expire_tokens > 0:
            self.expire_tokens -= 1
            return _error(401, "authError")
        actor = request.headers.get("Authorization", "").removeprefix("Bearer at:")
        params = request.url.params
        if path == "files":
            match = _PARENT_QUERY.search(params.get("q", ""))
            parent = match.group(1) if match else None
            children = [self._json(f) for f in self.files.values()
                        if parent in f["parents"] and not f["trashed"] and self._role(actor, f)]
            return httpx.Response(200, json={"files": children})
        file_id, _, rest = path.removeprefix("files/").partition("/")
        file = self._visible(actor, file_id)
        if file is None:
            return _error(404, "notFound")
        if rest == "permissions":
            if self._role(actor, file) not in ("owner", "writer"):
                return _error(403, "insufficientFilePermissions")
            return httpx.Response(200, json={"permissions": self._effective(file)})
        if rest == "export" or params.get("alt") == "media":
            return httpx.Response(200, content=("﻿" + file["content"].replace("\n", "\r\n")).encode())
        return httpx.Response(200, json=self._json(file))


ADMIN = "drive.admin@example.com"     # owns the seeded files; not in the identity map, so it holds no token
GROUP_ADDRESS = "payments-eng@groups.example.com"


def seed_company_a(drive: FakeDrive, data: dict) -> tuple[dict[str, DriveSession], IdentityMap, DriveConfig]:
    """Build the Company A story in `drive`. Returns (sessions, identity map, config) for a connector over it.

    Folders `incidents` and `vendor` are shared with the payments-eng group, so their files inherit it. The
    postmortem is also shared with Dana and the vendor notes with Sam, an outside address. Priya, Dana and Maya
    have signed in; the admin account that owns everything has too, which is how the sharing lists can be read.
    """
    accounts = {p["email"]: {"gdrive": f"{p['id']}.account@example.com"} for p in data["personas"]}
    address = {email: per["gdrive"] for email, per in accounts.items()}
    members = [p["email"] for p in data["personas"] if "group:gdrive:payments-eng" in p["tokens"]]
    drive.group_members[GROUP_ADDRESS] = {address[m] for m in members}
    for doc in (d for d in data["documents"] if d["source"] == "gdrive"):
        folder = doc["parent_id"].split(":", 1)[1]
        if folder not in drive.files:
            drive.add_folder(folder, doc["acl"]["native"]["folder"], owner=ADMIN)
            drive.share(folder, GROUP_ADDRESS, kind="group")
        drive.add(doc["doc_id"].split(":", 1)[1], doc["title"], owner=ADMIN, parent=folder, content=doc["body"], modified=doc["updated_at"])
        for token in doc["acl"]["tokens"]:
            kind, _, who = token.partition(":")
            if kind in ("user", "external"):
                drive.share(doc["doc_id"].split(":", 1)[1], address[who])
    config = DriveConfig(root_folders=sorted({d["parent_id"].split(":", 1)[1] for d in data["documents"] if d["source"] == "gdrive"}),
                         groups={"payments-eng": Group(frozenset(members), GROUP_ADDRESS)}, org_domains=["companya.com"])
    signed_in = [p["email"] for p in data["personas"] if p["id"] in ("priya", "dana", "maya")]
    sessions = {email: drive.session(address[email]) for email in signed_in} | {"admin": drive.session(ADMIN)}
    return sessions, IdentityMap(accounts), config

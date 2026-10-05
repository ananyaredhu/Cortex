"""Connector for Google Drive on personal accounts (docs/02-contracts/connector-interface.md, acl-model.md).

One document is one file. `doc_id` is `gdrive:<file id>`, `parent_id` is `gdrive:<folder id>`, `version` is the
file's modified time (so a sharing change alone does not look like a content change).

How it gets at Drive: through the read-only OAuth grants of the persona accounts that signed in (client.py). A
file exists for the connector when it sits under a configured root folder (config.py) and at least one of those
accounts can read its full sharing list. Drive only shows that list to people who can share the file, so in
practice the owner or an editor must be among the signed-in accounts. A file whose sharing cannot be read is
not indexed: without ACL evidence it cannot be served safely.

Tokens come from the sharing list, which already includes what the file inherits from its folders:
- a person: `user:<canonical email>`, or `external:<canonical email>` outside the organisation's domains;
- a Google Group named in the config: `group:gdrive:<name>`;
- every member of a configured group shared one by one: the same `group:gdrive:<name>`, in place of their own
  tokens (personal accounts often have no real group);
- an account that is not in the identity map, "anyone with the link", and unknown groups: no token.

`check_access` reads the sharing list live and decides from it. Group membership is the one thing it cannot ask
Drive: that comes from the local config.

Changes are found by polling, like the Slack connector: scan the root folders, compare with the previous scan.
"""
import hashlib
import json
import re
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone

from connectors.base import AccessDecision, AclEvidence, Change, ChangeBatch, Document, DocumentNotFound, PlatformIdentity, Source
from connectors.env import load_dotenv
from connectors.gdrive.client import DriveError, DriveSession, load_sessions
from connectors.gdrive.config import DriveConfig
from connectors.identity_map import IdentityMap

POLICY_VERSION = "gdrive-0.1"
ACCESS_TIMEOUT = 3.0
FOLDER = "application/vnd.google-apps.folder"
GOOGLE_DOC = "application/vnd.google-apps.document"
FILE_FIELDS = "id,name,mimeType,parents,trashed,modifiedTime,createdTime,webViewLink,owners(emailAddress)"
PERMISSION_FIELDS = "nextPageToken,permissions(id,type,role,emailAddress,deleted)"
MAX_DEPTH = 25

_FILE_ID = re.compile(r"^[A-Za-z0-9_-]{5,}$")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(doc_id: str) -> str | None:
    """"gdrive:<file id>" -> file id. None if it is not a well-formed Drive doc id."""
    prefix, _, file_id = doc_id.partition(":")
    return file_id if prefix == "gdrive" and _FILE_ID.match(file_id) else None


class _NotVisible(Exception):
    """No signed-in account can do this."""


@dataclass
class _Acl:
    evidence: AclEvidence
    proof: dict[str, str] = field(default_factory=dict)   # canonical email -> the token that lets them read


class DriveConnector:
    source: Source = "gdrive"

    def __init__(self, sessions: dict[str, DriveSession], identities: IdentityMap, config: DriveConfig, *,
                 policy_version: str = POLICY_VERSION, access_timeout: float = ACCESS_TIMEOUT) -> None:
        """`sessions` maps a label (the canonical email of whoever signed in) to that account's session."""
        self._sessions = dict(sessions)
        self._identities = identities
        self._config = config
        self._policy_version = policy_version
        self._access_timeout = access_timeout
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="gdrive-access")
        self._via: dict[tuple[str, str], str] = {}     # (what, file id) -> label of the session that last managed it
        self._folder_parent: dict[str, str | None] = {}
        self._epoch = uuid.uuid4().hex[:8]
        self._baseline: dict[str, tuple] | None = None
        self._log: list[Change] = []

    @classmethod
    def from_env(cls) -> "DriveConnector":
        """Sessions from the token files, identity map and config from their local files."""
        load_dotenv()
        return cls(load_sessions(), IdentityMap.load(), DriveConfig.load())

    # -- Connector protocol ---------------------------------------------------------------------
    def resolve_identity(self, email: str) -> PlatformIdentity | None:
        """None when the canonical email has no mapped Google account (fail closed).

        Tokens are the configured groups the person is in, plus `external:` for someone outside the organisation.
        Drive has no API a consumer account can ask for this, so it is local configuration, not a live lookup.
        """
        try:
            account = self._identities.platform_account(self.source, email)
            if account is None:
                return None
            canonical = email.strip().lower()
            tokens = [f"group:gdrive:{name}" for name in self._config.groups_of(canonical)]
            if not self._config.is_internal(canonical):
                tokens.append(f"external:{canonical}")
            return PlatformIdentity(self.source, account, email, sorted(tokens))
        except Exception:   # fail closed on anything
            return None

    def list_changes(self, cursor: str | None) -> ChangeBatch:
        """cursor=None is the full crawl: every file as an upsert. Otherwise what changed since that cursor."""
        scan = self._scan()
        if self._baseline is not None:
            self._log += self._diff(self._baseline, scan)
        self._baseline = scan
        end = f"{self._epoch}:{len(self._log)}"
        epoch, _, raw = (cursor or "").partition(":")
        if cursor is None or epoch != self._epoch or not raw.isdigit() or int(raw) > len(self._log):
            now = _now()
            return ChangeBatch([Change("upsert", doc_id, now) for doc_id in sorted(scan)], end, False)
        return ChangeBatch(self._log[int(raw):], end, False)

    def fetch(self, doc_id: str) -> Document:
        label, meta = self._document(doc_id)
        try:
            acl = self._evaluate(meta, self._permissions(meta["id"]))
        except _NotVisible:
            raise DocumentNotFound(doc_id) from None    # no ACL evidence, so it cannot be served
        owner = ((meta.get("owners") or [{}])[0]).get("emailAddress") or ""
        return Document(
            doc_id=doc_id,
            source=self.source,
            kind="file",
            title=meta.get("name") or "",
            url=meta.get("webViewLink") or f"https://drive.google.com/file/d/{meta['id']}/view",
            body=self._content(label, meta),
            parent_id=f"gdrive:{meta['parents'][0]}" if meta.get("parents") else None,
            links=[],
            author=self._identities.canonical_email(self.source, owner) if owner else None,
            created_at=meta.get("createdTime") or "",
            updated_at=meta.get("modifiedTime") or "",
            version=meta.get("modifiedTime") or "",
            acl=acl.evidence,
        )

    def check_access(self, identity: PlatformIdentity, doc_id: str) -> AccessDecision:
        """Live answer from the file's sharing list as Drive has it now. `identity.groups` is not trusted."""
        deny = AccessDecision(False, [], _now(), "", self._policy_version)
        try:
            file_id = _parse(doc_id)
            canonical = identity.email.strip().lower()
            account = self._identities.platform_account(self.source, canonical)
            if file_id is None or identity.source != self.source or account is None \
                    or account != identity.platform_user_id.strip().lower():
                return deny
            deadline = time.monotonic() + self._access_timeout

            def answer(future: Future):
                return future.result(timeout=max(0.0, deadline - time.monotonic()))

            asked_meta = self._pool.submit(self._meta, file_id, retry=False)
            asked_permissions = self._pool.submit(self._permissions, file_id, retry=False)
            label, meta = answer(asked_meta)
            if not self._is_document(meta) or not self._in_scope(meta, retry=False):
                return deny
            acl = self._evaluate(meta, answer(asked_permissions))
            token = acl.proof.get(canonical)
            if token is None:
                return AccessDecision(False, [], _now(), acl.evidence.snapshot_hash, self._policy_version)
            principal = f"user:{canonical}"
            proof = [principal] if token == principal else [principal, token]
            return AccessDecision(True, proof, _now(), acl.evidence.snapshot_hash, self._policy_version)
        except Exception:   # fail closed on anything: network, timeout, rate limit, malformed response, not visible
            return deny

    def version(self, doc_id: str) -> str:
        return self._document(doc_id)[1].get("modifiedTime") or ""

    # -- reading Drive through whichever account can ----------------------------------------------
    def _try(self, what: str, file_id: str, call):
        """Run `call(session)` with each signed-in account, the one that worked last time first.
        Returns (label, result). Raises _NotVisible when none of them may."""
        preferred = self._via.get((what, file_id))
        labels = ([preferred] if preferred in self._sessions else []) + [label for label in self._sessions if label != preferred]
        for label in labels:
            try:
                result = call(self._sessions[label])
            except DriveError as exc:
                if exc.not_visible:
                    continue
                raise
            self._via[(what, file_id)] = label
            return label, result
        raise _NotVisible(file_id)

    def _meta(self, file_id: str, *, retry: bool = True) -> tuple[str, dict]:
        return self._try("meta", file_id, lambda s: s.json(f"files/{file_id}", retry=retry, fields=FILE_FIELDS))

    def _permissions(self, file_id: str, *, retry: bool = True) -> list[dict]:
        return self._try("permissions", file_id, lambda s: list(s.pages(
            f"files/{file_id}/permissions", "permissions", retry=retry, fields=PERMISSION_FIELDS, pageSize=100)))[1]

    @staticmethod
    def _is_document(meta: dict) -> bool:
        return meta.get("mimeType") != FOLDER and not meta.get("trashed")

    def _in_scope(self, meta: dict, *, retry: bool = True) -> bool:
        """Is the file under a configured root folder? Anything else must stay invisible."""
        folder = (meta.get("parents") or [None])[0]
        for _ in range(MAX_DEPTH):
            if folder is None:
                return False
            if folder in self._config.root_folders:
                return True
            if folder not in self._folder_parent:
                try:
                    parents = self._try("meta", folder, lambda s, f=folder: s.json(f"files/{f}", retry=retry, fields="id,parents"))[1]
                except _NotVisible:
                    return False
                self._folder_parent[folder] = (parents.get("parents") or [None])[0]
            folder = self._folder_parent[folder]
        return False

    def _document(self, doc_id: str) -> tuple[str, dict]:
        """(label of the account that can read it, metadata). Raises DocumentNotFound for anything that is not a visible, in-scope file."""
        file_id = _parse(doc_id)
        if file_id is None:
            raise DocumentNotFound(doc_id)
        try:
            label, meta = self._meta(file_id)
        except _NotVisible:
            raise DocumentNotFound(doc_id) from None
        if not self._is_document(meta) or not self._in_scope(meta):
            raise DocumentNotFound(doc_id)
        return label, meta

    def _content(self, label: str, meta: dict) -> str:
        """Text of the file. Google Docs are exported as plain text; other text files are read as they are.
        Anything else (PDF, images, Sheets...) has no body here and is indexed by its title."""
        session = self._sessions[label]
        mime = meta.get("mimeType") or ""
        if mime == GOOGLE_DOC:
            text = session.text(f"files/{meta['id']}/export", mimeType="text/plain")
        elif mime.startswith("text/"):
            text = session.text(f"files/{meta['id']}", alt="media")
        else:
            return ""
        return text.lstrip("﻿").replace("\r\n", "\n").strip()

    # -- permissions -> tokens --------------------------------------------------------------------
    def _individual(self, canonical: str) -> str:
        return f"user:{canonical}" if self._config.is_internal(canonical) else f"external:{canonical}"

    def _evaluate(self, meta: dict, permissions: list[dict]) -> _Acl:
        people: dict[str, str] = {}      # canonical email -> role
        granted: set[str] = set()        # configured groups the file is shared with by address
        sharing: list[dict] = []         # native evidence, with canonical principals only: no real addresses stored
        for permission in permissions:
            if permission.get("deleted"):
                continue
            kind, role = permission.get("type"), permission.get("role")
            address = permission.get("emailAddress") or ""
            if kind == "user":
                canonical = self._identities.canonical_email(self.source, address) if address else None
                if canonical:
                    people[canonical] = role
                sharing.append({"type": "user", "role": role, "principal": canonical})
            elif kind == "group":
                name = self._config.group_by_address(address) if address else None
                if name:
                    granted.add(name)
                sharing.append({"type": "group", "role": role, "principal": name})
            else:
                sharing.append({"type": kind, "role": role, "principal": None})   # anyone / domain: no token
        inferred = {name for name, group in self._config.groups.items()
                    if name not in granted and group.members and group.members <= set(people)}
        covered = set().union(*(self._config.groups[name].members for name in inferred)) if inferred else set()

        proof: dict[str, str] = {}
        for canonical in people:
            via = next((name for name in sorted(inferred) if canonical in self._config.groups[name].members), None)
            proof[canonical] = f"group:gdrive:{via}" if via else self._individual(canonical)
        for name in sorted(granted):
            for member in self._config.groups[name].members:
                proof.setdefault(member, f"group:gdrive:{name}")
        tokens = sorted({f"group:gdrive:{name}" for name in granted | inferred}
                        | {self._individual(c) for c in people if c not in covered})
        native = {"folder": (meta.get("parents") or [None])[0],
                  "sharing": sorted(sharing, key=lambda s: json.dumps(s, sort_keys=True)),
                  "inferred_groups": sorted(inferred)}
        canonical_json = json.dumps(native, sort_keys=True, separators=(",", ":"))
        evidence = AclEvidence(tokens, native, "sha256:" + hashlib.sha256(canonical_json.encode()).hexdigest(), _now())
        return _Acl(evidence, proof)

    # -- change detection -------------------------------------------------------------------------
    def _scan(self) -> dict[str, tuple]:
        """Every in-scope file whose sharing can be read: doc id -> (modified time, ACL hash, tokens)."""
        found: dict[str, dict] = {}
        queue, seen = list(self._config.root_folders), set(self._config.root_folders)
        self._folder_parent = {}
        while queue:
            folder = queue.pop(0)
            for session in self._sessions.values():
                try:
                    children = list(session.pages("files", "files", q=f"'{folder}' in parents and trashed = false",
                                                  fields=f"nextPageToken,files({FILE_FIELDS})", pageSize=200))
                except DriveError as exc:
                    if exc.not_visible:
                        continue
                    raise
                for child in children:
                    if child.get("mimeType") == FOLDER:
                        self._folder_parent[child["id"]] = folder
                        if child["id"] not in seen and len(seen) < 10_000:
                            seen.add(child["id"])
                            queue.append(child["id"])
                    elif self._is_document(child):
                        found.setdefault(child["id"], child)
        scan: dict[str, tuple] = {}
        for file_id, meta in found.items():
            try:
                acl = self._evaluate(meta, self._permissions(file_id))
            except _NotVisible:
                continue   # nobody signed in can read its sharing: not indexed
            scan[f"gdrive:{file_id}"] = (meta.get("modifiedTime"), acl.evidence.snapshot_hash, tuple(acl.evidence.tokens))
        return scan

    @staticmethod
    def _diff(old: dict[str, tuple], new: dict[str, tuple]) -> list[Change]:
        now = _now()
        changes: list[Change] = []
        for doc_id in sorted(new):
            if doc_id not in old or old[doc_id][0] != new[doc_id][0]:
                changes.append(Change("upsert", doc_id, now))
            elif old[doc_id][1:] != new[doc_id][1:]:
                changes.append(Change("acl_change", doc_id, now))   # shared, unshared, or a folder's sharing changed
        changes += [Change("delete", doc_id, now) for doc_id in sorted(set(old) - set(new))]
        return changes

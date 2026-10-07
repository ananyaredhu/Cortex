"""Shared connector types. Mirrors docs/02-contracts/connector-interface.md (v0.2).

Real connectors, simulators and the fixture connector all implement `Connector`.
Change the contract doc first (PR with all three reviewers), then this file.
"""
from dataclasses import dataclass, field
from typing import Literal, Protocol

Source = Literal["slack", "gdrive", "confluence", "jira"]


class DocumentNotFound(LookupError):
    """Raised by `fetch` and `version`. Never raised by `check_access`, which returns a deny instead."""


class CursorExpired(LookupError):
    """Raised by `list_changes` when the source no longer knows the cursor (e.g. a simulator restarted and its change
    log began again). The consumer starts over with a full crawl, `list_changes(None)`."""


@dataclass
class PlatformIdentity:
    source: Source
    platform_user_id: str
    email: str                                        # canonical (IdP) email, not the platform account's address
    groups: list[str] = field(default_factory=list)   # ACL tokens held on this platform; the PDP adds the user: token


@dataclass
class AclEvidence:
    tokens: list[str]
    native: dict
    snapshot_hash: str
    observed_at: str


@dataclass
class Document:
    doc_id: str
    source: Source
    kind: str
    title: str
    url: str
    body: str
    parent_id: str | None
    links: list[str]
    author: str | None            # canonical email when the platform account is mapped, else None
    created_at: str
    updated_at: str
    version: str
    acl: AclEvidence


@dataclass
class Change:
    """One entry of `list_changes`.

    upsert / delete / acl_change name a document: `doc_id` is set, `principal` and `token` are None.
    principal_change means a person's token set changed (left a channel, group or role): `doc_id` is None,
    `principal` is "user:<canonical email>" and `token` is the token gained or lost. No document's tokens
    changed, so nothing is re-indexed; drop cached identities and decisions for that principal.
    """
    type: Literal["upsert", "delete", "acl_change", "principal_change"]
    doc_id: str | None
    detected_at: str
    principal: str | None = None
    token: str | None = None


@dataclass
class ChangeBatch:
    changes: list[Change]
    next_cursor: str
    has_more: bool = False


@dataclass
class AccessDecision:
    """Stays inside the policy plane: never serialized to the LLM plane or to the user."""
    allowed: bool
    proof_path: list[str]
    evaluated_at: str
    acl_snapshot_hash: str
    policy_version: str


class Connector(Protocol):
    source: Source

    def resolve_identity(self, email: str) -> PlatformIdentity | None:
        """Canonical email -> this platform's identity. None when unmapped (fail closed)."""

    def list_changes(self, cursor: str | None) -> ChangeBatch:
        """cursor=None starts a full crawl (every document as an upsert, paged). Otherwise incremental changes.
        Raises CursorExpired when the cursor is no longer valid."""

    def fetch(self, doc_id: str) -> Document:
        """Raises DocumentNotFound if the document does not exist or was deleted."""

    def check_access(self, identity: PlatformIdentity, doc_id: str) -> AccessDecision:
        """Live, authoritative. Never allows on an error or timeout (fail closed)."""

    def version(self, doc_id: str) -> str:
        """Raises DocumentNotFound like `fetch`."""

"""Shared connector types. Mirrors docs/02-contracts/connector-interface.md (0.6).

Real connectors, simulators and the fixture connector all implement `Connector`.
Change the contract doc first (PR with all three reviewers), then this file.
"""
from dataclasses import dataclass, field
from typing import Literal, Protocol

Source = Literal["slack", "gdrive", "confluence", "jira"]


class DocumentNotFound(LookupError):
    """Raised by `fetch` and `version`. Never raised by `check_access`, which returns a deny instead."""


class TransitionRefused(Exception):
    """Raised by `WritableConnector.transition` when the platform will not make the move: its workflow offers no such
    transition from the issue's current status, or it denies this identity. Carries nothing beyond the issue itself."""


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
    # What the platform holds as a field, not as text: lower-case name -> value, both strings ({} when it has none).
    # Jira: `status`, `assignee` (display name), and (0.5) `due` (YYYY-MM-DD), `priority`, `assignee_email` (canonical,
    # only when the assignee's account is mapped). Source data like `body`: untrusted, sanitized before a model sees it.
    fields: dict[str, str] = field(default_factory=dict)


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


class WritableConnector(Connector, Protocol):
    """(0.5) The one write in the interface, implemented for Jira only: connector-interface.md, "Writing"."""

    def transition(self, identity: PlatformIdentity, doc_id: str, status: str) -> Document:
        """Move one issue to `status` as `identity` and return it as the platform now holds it. `status` is matched
        without regard to case against the transitions the platform offers. Raises DocumentNotFound, or
        TransitionRefused; any other error means nothing is known to have changed."""


# -- (0.6) Creating items: connector-interface.md, "Writing: creating items" ----------------------------------------
class WriteRefused(Exception):
    """Raised by `CreatingConnector.create` and `delete_created` when the platform will not do it for this identity.
    Carries nothing beyond the destination."""


@dataclass(frozen=True)
class Audience:
    """Who can read what is created at a destination. Stays inside the policy plane, like `AccessDecision`."""
    principals: tuple[str, ...] | None   # canonical emails of every reader; None when they cannot all be listed
    has_external: bool                   # some reader is a guest or external person
    snapshot_hash: str                   # changes whenever the audience does


@dataclass(frozen=True)
class CreateOp:
    """One operation of a confirmed plan, already decided by the policy plane."""
    type: str                            # an action type, e.g. "jira.create_issue"
    destination: str                     # a container id: "jira:DBMIG", "slack:C_DBMIG", "slack:C_DBMIG/<ts>", ...
    fields: dict[str, str]
    assignee_account: str | None = None  # resolved by the policy plane, never by the model


class CreatingConnector(Connector, Protocol):
    """(0.6) Creating items as the person. `create` and `delete_created` arrive with step 3 of the actions build order."""

    def can_create(self, identity: PlatformIdentity, destination: str) -> bool:
        """Live: may this identity create in `destination` now? False for an unknown or invisible destination and on
        any error (fail closed). Never raises DocumentNotFound."""

    def audience(self, destination: str) -> Audience:
        """Every reader of what is created in `destination`. Raises DocumentNotFound for an unknown destination."""

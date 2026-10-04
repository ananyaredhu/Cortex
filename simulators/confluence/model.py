"""In-memory Confluence model with faithful view-permission semantics.

This is the policy logic of the simulator: plain, deterministic code with no I/O.
Rules implemented (docs/02-contracts/acl-model.md, "Per-source semantics"):

- A **space** grants view access to users and groups.
- A **page** may carry a read restriction (users and groups). A restriction only NARROWS access:
  it never grants access to someone the space does not admit.
- Restrictions **inherit**: to read a page, a user must pass the restriction of every restricted
  ancestor as well as the page's own.
- So a user may read a page when they hold the space's view permission AND satisfy every
  restriction on the path from the root page down to the page.

Normalized tokens follow the contract's narrowing rule: a restricted page carries the tokens of its
innermost restriction; an unrestricted page carries the space's. That set is never smaller than the
true readers, and `can_view` is the authoritative answer.

Every mutation appends to a change log, which backs `list_changes` and the webhooks. The two kinds of
permission change in the contract are kept apart: a page's own ACL changing is an `acl_change` on each
affected page; a person joining or leaving a group is a single `principal_change`.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from simulators.common import ChangeEntry, ChangeLog, Invalid, NotFound, norm_email, utc_now
from simulators.confluence.tokens import group_token, user_token

__all__ = ["ConfluenceSim", "Invalid", "NotFound", "Page", "Principals", "Space", "User", "ViewDecision", "doc_id", "valid_id"]

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_VERSION_LABEL = re.compile(r"^v(\d+)$")


def valid_id(value: str) -> bool:
    return bool(_ID.match(value))


@dataclass
class User:
    account_id: str
    email: str
    display_name: str


@dataclass
class Principals:
    users: set[str] = field(default_factory=set)   # emails
    groups: set[str] = field(default_factory=set)  # group names

    def __bool__(self) -> bool:
        return bool(self.users or self.groups)

    def tokens(self) -> list[str]:
        return sorted({user_token(u) for u in self.users} | {group_token(g) for g in self.groups})

    def as_json(self) -> dict:
        return {"groups": sorted(self.groups), "users": sorted(self.users)}


@dataclass
class Space:
    key: str
    name: str
    view: Principals = field(default_factory=Principals)


@dataclass
class Page:
    id: str
    space_key: str
    title: str
    body: str
    parent_id: str | None
    author: str | None
    created_at: str
    updated_at: str
    version: str                 # opaque label returned as the source version
    number: int = 1              # Confluence's integer version number
    restrictions: Principals = field(default_factory=Principals)
    links: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)


def doc_id(page: Page) -> str:
    return f"confluence:{page.space_key}/{page.id}"


@dataclass
class ViewDecision:
    allowed: bool
    grant_path: list[str]        # tokens, starting with the user's; empty on deny


class ConfluenceSim:
    def __init__(self, clock: Callable[[], str] = utc_now) -> None:
        self._clock = clock
        self.users: dict[str, User] = {}            # by email
        self._by_account: dict[str, User] = {}
        self.groups: dict[str, set[str]] = {}       # group name -> member emails
        self.spaces: dict[str, Space] = {}
        self.pages: dict[str, Page] = {}
        self.changelog = ChangeLog(clock)

    def clear(self) -> None:
        self.users.clear()
        self._by_account.clear()
        self.groups.clear()
        self.spaces.clear()
        self.pages.clear()
        self.changelog.clear()

    # ------------------------------------------------------------------ users and groups
    def add_user(self, email: str, display_name: str | None = None) -> User:
        email = norm_email(email)
        if email not in self.users:
            account_id = "cf-" + hashlib.sha256(email.encode()).hexdigest()[:16]
            user = User(account_id, email, display_name or email.split("@")[0])
            self.users[email] = user
            self._by_account[account_id] = user
        return self.users[email]

    def user_by_account(self, account_id: str) -> User | None:
        return self._by_account.get(account_id)

    def groups_of(self, email: str) -> list[str]:
        email = norm_email(email)
        return sorted(g for g, members in self.groups.items() if email in members)

    def add_member(self, group: str, email: str) -> bool:
        email = norm_email(email)
        if email not in self.users:
            raise NotFound(f"No user with email: {email}")
        members = self.groups.setdefault(group, set())
        if email in members:
            return False
        members.add(email)
        self.changelog.emit_principal(user_token(email), group_token(group), "group_membership_updated")
        return True

    def remove_member(self, group: str, email: str) -> bool:
        """Revocation by membership: no page's ACL changes, so this is one principal_change."""
        email = norm_email(email)
        if email not in self.groups.get(group, set()):
            return False
        self.groups[group].discard(email)
        self.changelog.emit_principal(user_token(email), group_token(group), "group_membership_updated")
        return True

    # ------------------------------------------------------------------ spaces
    def add_space(self, key: str, name: str | None = None) -> Space:
        if not valid_id(key):
            raise Invalid(f"Bad space key: {key!r}")
        if key not in self.spaces:
            self.spaces[key] = Space(key, name or key)
        elif name:
            self.spaces[key].name = name
        return self.spaces[key]

    def set_space_view(self, key: str, groups: Iterable[str] = (), users: Iterable[str] = ()) -> None:
        """Replace who may view the space. Every page in the space gets an acl_change."""
        space = self._space(key)
        new = Principals({norm_email(u) for u in users}, set(groups))
        if new == space.view:
            return
        space.view = new
        for page in self.pages.values():
            if page.space_key == key:
                self._emit("acl_change", page, "space_permissions_updated")

    def _space(self, key: str) -> Space:
        if key not in self.spaces:
            raise NotFound(f"No space with key: {key}")
        return self.spaces[key]

    # ------------------------------------------------------------------ pages
    def create_page(self, page_id: str, space_key: str, title: str, body: str = "", *, parent_id: str | None = None,
                    author: str | None = None, groups: Iterable[str] = (), users: Iterable[str] = (),
                    links: Iterable[str] = (), labels: Iterable[str] = (), created_at: str | None = None,
                    updated_at: str | None = None, version: str | None = None) -> Page:
        if not valid_id(page_id):
            raise Invalid(f"Bad page id: {page_id!r}")
        if page_id in self.pages:
            raise Invalid(f"A page with this id already exists: {page_id}")
        self._space(space_key)
        if parent_id is not None and self._page(parent_id).space_key != space_key:
            raise Invalid("The parent page is in a different space")
        now = self._clock()
        page = Page(
            id=page_id, space_key=space_key, title=title, body=body, parent_id=parent_id, author=author,
            created_at=created_at or now, updated_at=updated_at or created_at or now, version=version or "v1",
            restrictions=Principals({norm_email(u) for u in users}, set(groups)), links=list(links), labels=list(labels),
        )
        self.pages[page_id] = page
        self._emit("upsert", page, "page_created")
        return page

    def update_page(self, page_id: str, *, title: str | None = None, body: str | None = None,
                    links: Iterable[str] | None = None, version: str | None = None,
                    updated_at: str | None = None) -> Page:
        page = self._page(page_id)
        if title is not None:
            page.title = title
        if body is not None:
            page.body = body
        if links is not None:
            page.links = list(links)
        page.updated_at = updated_at or self._clock()
        page.number += 1
        page.version = version or self._next_version(page)
        self._emit("upsert", page, "page_updated")
        return page

    @staticmethod
    def _next_version(page: Page) -> str:
        """Seeded pages keep whatever label the seed gave them; later edits must still change it."""
        match = _VERSION_LABEL.match(page.version)
        if match:
            return f"v{int(match.group(1)) + 1}"
        return page.updated_at if page.updated_at != page.version else f"{page.updated_at}#{page.number}"

    def delete_page(self, page_id: str) -> None:
        """Like Confluence, children move up to the deleted page's parent; they lose its restriction."""
        page = self._page(page_id)
        below = self.descendants(page_id)
        for child in self.pages.values():
            if child.parent_id == page_id:
                child.parent_id = page.parent_id
        del self.pages[page_id]
        self._emit("delete", page, "page_removed")
        if page.restrictions:
            for d in below:
                self._emit("acl_change", d, "content_permissions_updated")

    def set_restrictions(self, page_id: str, groups: Iterable[str] = (), users: Iterable[str] = ()) -> None:
        """Replace the page's read restriction (empty = unrestricted). The page and its whole subtree change."""
        page = self._page(page_id)
        new = Principals({norm_email(u) for u in users}, set(groups))
        if new == page.restrictions:
            return
        page.restrictions = new
        for p in [page, *self.descendants(page_id)]:
            self._emit("acl_change", p, "content_permissions_updated")

    def _page(self, page_id: str) -> Page:
        if page_id not in self.pages:
            raise NotFound(f"No content found with id: {page_id}")
        return self.pages[page_id]

    def ancestors(self, page: Page) -> list[Page]:
        """Root first, excluding the page itself."""
        out: list[Page] = []
        cur = page
        while cur.parent_id is not None and cur.parent_id in self.pages and len(out) <= len(self.pages):
            cur = self.pages[cur.parent_id]
            out.append(cur)
        return out[::-1]

    def descendants(self, page_id: str) -> list[Page]:
        children: dict[str | None, list[Page]] = {}
        for p in self.pages.values():
            children.setdefault(p.parent_id, []).append(p)
        out: list[Page] = []
        stack = list(children.get(page_id, []))
        while stack:
            p = stack.pop()
            out.append(p)
            stack.extend(children.get(p.id, []))
        return sorted(out, key=lambda p: p.id)

    def restriction_chain(self, page: Page) -> list[Page]:
        """Restricted ancestors and the page itself if restricted, root first."""
        return [p for p in [*self.ancestors(page), page] if p.restrictions]

    # ------------------------------------------------------------------ permission evaluation
    def can_view(self, email: str | None, page_id: str) -> ViewDecision:
        """Authoritative answer. Unknown users and unknown pages are denied the same way."""
        deny = ViewDecision(False, [])
        user = self.users.get(norm_email(email)) if email else None
        page = self.pages.get(page_id)
        if user is None or page is None:
            return deny
        memberships = set(self.groups_of(user.email))
        path = [user_token(user.email)]
        gates = [self.spaces[page.space_key].view, *(p.restrictions for p in self.restriction_chain(page))]
        for gate in gates:
            grant = _grant(gate, user.email, memberships)
            if grant is None:
                return deny
            if grant not in path:
                path.append(grant)
        return ViewDecision(True, path)

    def acl(self, page: Page) -> dict:
        """ACL evidence for the contract's AclEvidence: normalized tokens plus the native data they came from."""
        space = self.spaces[page.space_key]
        chain = self.restriction_chain(page)
        effective = chain[-1].restrictions if chain else space.view
        native = {
            "space": space.key,
            "space_view": space.view.as_json(),
            "restrictions": [{"page": p.id, **p.restrictions.as_json()} for p in chain],
        }
        canonical = json.dumps(native, sort_keys=True, separators=(",", ":"))
        return {
            "tokens": effective.tokens(),
            "native": native,
            "snapshot_hash": "sha256:" + hashlib.sha256(canonical.encode()).hexdigest(),
        }

    # ------------------------------------------------------------------ change feed
    def _emit(self, type_: str, page: Page, event: str) -> None:
        self.changelog.emit(type_, page.id, doc_id(page), event)

    def changes(self, cursor: str | None, limit: int = 500) -> tuple[list[ChangeEntry], str, bool]:
        """(entries, next_cursor, has_more). cursor=None starts a full crawl: every page as an upsert."""
        return self.changelog.read(cursor, limit, {p.id: doc_id(p) for p in self.pages.values()})


def _grant(gate: Principals, email: str, memberships: set[str]) -> str | None:
    """The token through which `email` passes this gate, or None."""
    if email in gate.users:
        return user_token(email)
    matched = sorted(gate.groups & memberships)
    return group_token(matched[0]) if matched else None

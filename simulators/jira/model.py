"""In-memory Jira model with faithful browse-permission semantics.

This is the policy logic of the simulator: plain, deterministic code with no I/O.
Rules implemented (docs/02-contracts/acl-model.md, "Per-source semantics"):

- A **project** has roles. A role's actors are users and groups, per project.
- The project's permission scheme grants **Browse** to project roles, groups and users.
- A project may define **issue security levels**. Each level lists its members (roles, groups, users).
  An issue with a level is visible only to people who hold Browse AND are members of the level:
  a level only NARROWS access, it never grants it.
- A **sub-task** takes its parent's security level and cannot set its own.

Normalized tokens follow the contract's narrowing rule: an issue with a security level carries the
level's member tokens; an issue without one carries the Browse grants. That set is never smaller than
the true readers, and `can_view` is the authoritative answer.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from simulators.common import ChangeEntry, ChangeLog, Invalid, NotFound, norm_email, utc_now
from simulators.jira.tokens import group_token, role_token, user_token

_PROJECT = re.compile(r"^[A-Z][A-Z0-9]{0,19}$")
_ISSUE = re.compile(r"^([A-Z][A-Z0-9]{0,19})-([1-9]\d{0,8})$")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_VERSION_LABEL = re.compile(r"^v(\d+)$")


def valid_issue_key(key: str) -> bool:
    return bool(_ISSUE.match(key))


def doc_id(key: str) -> str:
    return f"jira:{key}"


@dataclass
class User:
    account_id: str
    email: str
    display_name: str


@dataclass
class Actors:
    """Who fills a project role."""
    users: set[str] = field(default_factory=set)    # emails
    groups: set[str] = field(default_factory=set)

    def as_json(self) -> dict:
        return {"groups": sorted(self.groups), "users": sorted(self.users)}


@dataclass
class Grants:
    """Who a permission or a security level is given to."""
    users: set[str] = field(default_factory=set)    # emails
    groups: set[str] = field(default_factory=set)
    roles: set[str] = field(default_factory=set)    # role names in the owning project

    @classmethod
    def of(cls, roles: Iterable[str] = (), groups: Iterable[str] = (), users: Iterable[str] = ()) -> Grants:
        return cls({norm_email(u) for u in users}, set(groups), set(roles))

    def tokens(self, project_key: str) -> list[str]:
        return sorted({user_token(u) for u in self.users} | {group_token(g) for g in self.groups}
                      | {role_token(project_key, r) for r in self.roles})

    def as_json(self) -> dict:
        return {"groups": sorted(self.groups), "roles": sorted(self.roles), "users": sorted(self.users)}


@dataclass
class Project:
    key: str
    name: str
    roles: dict[str, Actors] = field(default_factory=dict)
    browse: Grants = field(default_factory=Grants)
    levels: dict[str, Grants] = field(default_factory=dict)   # issue security scheme
    last_number: int = 0


@dataclass
class Comment:
    author: str | None
    body: str
    created_at: str


@dataclass
class Issue:
    key: str
    project_key: str
    summary: str
    description: str
    issue_type: str
    status: str | None
    reporter: str | None
    created_at: str
    updated_at: str
    version: str                     # opaque label returned as the source version
    number: int = 1
    security_level: str | None = None
    parent_key: str | None = None    # set for sub-tasks
    links: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    comments: list[Comment] = field(default_factory=list)


@dataclass
class ViewDecision:
    allowed: bool
    grant_path: list[str]            # tokens, starting with the user's; empty on deny


class JiraSim:
    def __init__(self, clock: Callable[[], str] = utc_now) -> None:
        self._clock = clock
        self.users: dict[str, User] = {}            # by email
        self._by_account: dict[str, User] = {}
        self.groups: dict[str, set[str]] = {}       # group name -> member emails
        self.projects: dict[str, Project] = {}
        self.issues: dict[str, Issue] = {}
        self.changelog = ChangeLog(clock)

    def clear(self) -> None:
        self.users.clear()
        self._by_account.clear()
        self.groups.clear()
        self.projects.clear()
        self.issues.clear()
        self.changelog.clear()

    # ------------------------------------------------------------------ users and groups
    def add_user(self, email: str, display_name: str | None = None) -> User:
        email = norm_email(email)
        if email not in self.users:
            account_id = "jira-" + hashlib.sha256(email.encode()).hexdigest()[:16]
            user = User(account_id, email, display_name or email.split("@")[0])
            self.users[email] = user
            self._by_account[account_id] = user
        return self.users[email]

    def user_by_account(self, account_id: str) -> User | None:
        return self._by_account.get(account_id)

    def _known(self, email: str) -> str:
        email = norm_email(email)
        if email not in self.users:
            raise NotFound(f"No user with email: {email}")
        return email

    def groups_of(self, email: str) -> list[str]:
        email = norm_email(email)
        return sorted(g for g, members in self.groups.items() if email in members)

    def add_member(self, group: str, email: str) -> bool:
        email = self._known(email)
        members = self.groups.setdefault(group, set())
        if email in members:
            return False
        before = self._tokens_held([email])
        members.add(email)
        self._emit_principal_changes(before, "group_membership_updated")
        return True

    def remove_member(self, group: str, email: str) -> bool:
        email = norm_email(email)
        if email not in self.groups.get(group, set()):
            return False
        before = self._tokens_held([email])
        self.groups[group].discard(email)
        self._emit_principal_changes(before, "group_membership_updated")
        return True

    def identity_tokens(self, email: str) -> set[str]:
        """Every token the user holds in Jira: their groups, and each project role they fill (directly or via a group)."""
        return {group_token(g) for g in self.groups_of(email)} | {role_token(p, r) for p, r in self.roles_of(email)}

    def _tokens_held(self, emails: Iterable[str]) -> dict[str, set[str]]:
        return {email: self.identity_tokens(email) for email in emails}

    def _emit_principal_changes(self, before: dict[str, set[str]], event: str) -> None:
        """One principal_change per token a user gained or lost. Leaving a group can also cost the roles it filled."""
        for email in sorted(before):
            for token in sorted(before[email] ^ self.identity_tokens(email)):
                self.changelog.emit_principal(user_token(email), token, event)

    # ------------------------------------------------------------------ projects, roles, schemes
    def add_project(self, key: str, name: str | None = None) -> Project:
        if not _PROJECT.match(key):
            raise Invalid(f"Bad project key: {key!r}")
        if key not in self.projects:
            self.projects[key] = Project(key, name or key)
        elif name:
            self.projects[key].name = name
        return self.projects[key]

    def _project(self, key: str) -> Project:
        if key not in self.projects:
            raise NotFound(f"No project could be found with key '{key}'")
        return self.projects[key]

    def set_role_actors(self, project_key: str, role: str, users: Iterable[str] = (), groups: Iterable[str] = ()) -> None:
        """Replace who fills a project role."""
        project = self._project(project_key)
        _check_name(role, "role")
        new = Actors({self._known(u) for u in users}, set(groups))
        if project.roles.get(role) == new:
            return
        before = self._tokens_held(self.users)
        project.roles[role] = new
        self._emit_principal_changes(before, "project_role_updated")

    def add_role_actor(self, project_key: str, role: str, email: str) -> bool:
        actors = self._project(project_key).roles.get(role, Actors())
        if norm_email(email) in actors.users:
            return False
        self.set_role_actors(project_key, role, actors.users | {email}, actors.groups)
        return True

    def remove_role_actor(self, project_key: str, role: str, email: str) -> bool:
        actors = self._project(project_key).roles.get(role, Actors())
        if norm_email(email) not in actors.users:
            return False
        self.set_role_actors(project_key, role, actors.users - {norm_email(email)}, actors.groups)
        return True

    def set_browse(self, project_key: str, roles: Iterable[str] = (), groups: Iterable[str] = (),
                   users: Iterable[str] = ()) -> None:
        """Replace who holds the Browse permission. Every issue in the project gets an acl_change."""
        project = self._project(project_key)
        new = Grants.of(roles, groups, users)
        if new == project.browse:
            return
        project.browse = new
        for issue in self._issues_of(project_key):
            self._emit("acl_change", issue, "project_permissions_updated")

    def set_security_level(self, project_key: str, level: str, roles: Iterable[str] = (), groups: Iterable[str] = (),
                           users: Iterable[str] = ()) -> None:
        """Define a security level, or replace its members. Issues at that level get an acl_change."""
        project = self._project(project_key)
        _check_name(level, "security level")
        new = Grants.of(roles, groups, users)
        if project.levels.get(level) == new:
            return
        project.levels[level] = new
        for issue in self._issues_of(project_key):
            if self.effective_level(issue) == level:
                self._emit("acl_change", issue, "issue_security_scheme_updated")

    def user_roles(self, email: str, project_key: str) -> set[str]:
        """Roles the user fills in the project, directly or through a group."""
        email = norm_email(email)
        memberships = set(self.groups_of(email))
        return {name for name, actors in self.projects[project_key].roles.items()
                if email in actors.users or actors.groups & memberships}

    def roles_of(self, email: str) -> list[tuple[str, str]]:
        return sorted((key, role) for key in self.projects for role in self.user_roles(email, key))

    # ------------------------------------------------------------------ issues
    def create_issue(self, project_key: str, summary: str, description: str = "", *, key: str | None = None,
                     issue_type: str = "Task", status: str | None = None, reporter: str | None = None,
                     security_level: str | None = None, parent_key: str | None = None, links: Iterable[str] = (),
                     labels: Iterable[str] = (), created_at: str | None = None, updated_at: str | None = None,
                     version: str | None = None) -> Issue:
        project = self._project(project_key)
        if key is None:
            number = project.last_number + 1
            key = f"{project_key}-{number}"
        else:
            match = _ISSUE.match(key)
            if not match or match.group(1) != project_key:
                raise Invalid(f"Bad issue key for project {project_key}: {key!r}")
            number = int(match.group(2))
        if key in self.issues:
            raise Invalid(f"An issue with this key already exists: {key}")
        if parent_key is not None:
            parent = self._issue(parent_key)
            if parent.project_key != project_key:
                raise Invalid("The parent issue is in a different project")
            if parent.parent_key is not None:
                raise Invalid("A sub-task cannot have sub-tasks")
            if security_level is not None:
                raise Invalid("A sub-task takes its parent's security level")
            issue_type = "Sub-task"
        if security_level is not None and security_level not in project.levels:
            raise Invalid(f"Project {project_key} has no security level {security_level!r}")
        now = self._clock()
        issue = Issue(
            key=key, project_key=project_key, summary=summary, description=description, issue_type=issue_type,
            status=status, reporter=reporter, created_at=created_at or now, updated_at=updated_at or created_at or now,
            version=version or "v1", security_level=security_level, parent_key=parent_key,
            links=list(links), labels=list(labels),
        )
        self.issues[key] = issue
        project.last_number = max(project.last_number, number)
        self._emit("upsert", issue, "jira:issue_created")
        return issue

    def update_issue(self, key: str, *, summary: str | None = None, description: str | None = None,
                     status: str | None = None, links: Iterable[str] | None = None, version: str | None = None,
                     updated_at: str | None = None) -> Issue:
        issue = self._issue(key)
        if summary is not None:
            issue.summary = summary
        if description is not None:
            issue.description = description
        if status is not None:
            issue.status = status
        if links is not None:
            issue.links = list(links)
        self._touch(issue, version, updated_at)
        self._emit("upsert", issue, "jira:issue_updated")
        return issue

    def add_comment(self, key: str, body: str, author: str | None = None) -> Issue:
        """Comments are part of the issue's document, so a new comment is an upsert."""
        issue = self._issue(key)
        issue.comments.append(Comment(author, body, self._clock()))
        self._touch(issue, None, None)
        self._emit("upsert", issue, "comment_created")
        return issue

    def _touch(self, issue: Issue, version: str | None, updated_at: str | None) -> None:
        issue.updated_at = updated_at or self._clock()
        issue.number += 1
        if version:
            issue.version = version
            return
        match = _VERSION_LABEL.match(issue.version)
        if match:
            issue.version = f"v{int(match.group(1)) + 1}"
        elif issue.updated_at != issue.version:
            issue.version = issue.updated_at
        else:   # seeded with a timestamp label and the clock has not moved: the version must still change
            issue.version = f"{issue.updated_at}#{issue.number}"

    def set_issue_security(self, key: str, level: str | None) -> None:
        """Set or clear the issue's security level. Its sub-tasks follow."""
        issue = self._issue(key)
        if issue.parent_key is not None:
            raise Invalid("A sub-task takes its parent's security level")
        if level is not None and level not in self.projects[issue.project_key].levels:
            raise Invalid(f"Project {issue.project_key} has no security level {level!r}")
        if level == issue.security_level:
            return
        issue.security_level = level
        for affected in [issue, *self.subtasks(key)]:
            self._emit("acl_change", affected, "issue_security_updated")

    def delete_issue(self, key: str) -> None:
        """Like Jira, deleting an issue deletes its sub-tasks."""
        issue = self._issue(key)
        for gone in [*self.subtasks(key), issue]:
            del self.issues[gone.key]
            self._emit("delete", gone, "jira:issue_deleted")

    def _issue(self, key: str) -> Issue:
        if key not in self.issues:
            raise NotFound("Issue does not exist or you do not have permission to see it.")
        return self.issues[key]

    def _issues_of(self, project_key: str) -> list[Issue]:
        return sorted((i for i in self.issues.values() if i.project_key == project_key), key=lambda i: i.key)

    def subtasks(self, key: str) -> list[Issue]:
        return sorted((i for i in self.issues.values() if i.parent_key == key), key=lambda i: i.key)

    def effective_level(self, issue: Issue) -> str | None:
        if issue.parent_key is not None and issue.parent_key in self.issues:
            return self.issues[issue.parent_key].security_level
        return issue.security_level

    # ------------------------------------------------------------------ permission evaluation
    def _gates(self, issue: Issue) -> list[Grants] | None:
        """Everything a reader must pass: Browse, then the security level if any. None = level is undefined."""
        project = self.projects[issue.project_key]
        level = self.effective_level(issue)
        if level is None:
            return [project.browse]
        if level not in project.levels:
            return None
        return [project.browse, project.levels[level]]

    def can_view(self, email: str | None, key: str) -> ViewDecision:
        """Authoritative answer. Unknown users and unknown issues are denied the same way."""
        deny = ViewDecision(False, [])
        user = self.users.get(norm_email(email)) if email else None
        issue = self.issues.get(key)
        if user is None or issue is None:
            return deny
        gates = self._gates(issue)
        if gates is None:
            return deny   # fail closed: the issue names a level the scheme does not define
        memberships = set(self.groups_of(user.email))
        roles = self.user_roles(user.email, issue.project_key)
        path = [user_token(user.email)]
        for gate in gates:
            grant = _grant(gate, issue.project_key, user.email, memberships, roles)
            if grant is None:
                return deny
            if grant not in path:
                path.append(grant)
        return ViewDecision(True, path)

    def acl(self, issue: Issue) -> dict:
        """ACL evidence for the contract's AclEvidence: normalized tokens plus the native data they came from."""
        project = self.projects[issue.project_key]
        level = self.effective_level(issue)
        members = project.levels.get(level) if level else None
        effective = project.browse if level is None else (members or Grants())
        native = {
            "project": project.key,
            "browse": project.browse.as_json(),
            "security_level": level,
            "level_members": members.as_json() if members else None,
            "inherited_from": issue.parent_key,
        }
        canonical = json.dumps(native, sort_keys=True, separators=(",", ":"))
        return {
            "tokens": effective.tokens(project.key),
            "native": native,
            "snapshot_hash": "sha256:" + hashlib.sha256(canonical.encode()).hexdigest(),
        }

    # ------------------------------------------------------------------ change feed
    def _emit(self, type_: str, issue: Issue, event: str) -> None:
        self.changelog.emit(type_, issue.key, doc_id(issue.key), event)

    def changes(self, cursor: str | None, limit: int = 500) -> tuple[list[ChangeEntry], str, bool]:
        return self.changelog.read(cursor, limit, {key: doc_id(key) for key in self.issues})


def _check_name(name: str, what: str) -> None:
    if not _NAME.match(name):
        raise Invalid(f"Bad {what} name: {name!r}")


def _grant(gate: Grants, project_key: str, email: str, memberships: set[str], roles: set[str]) -> str | None:
    """The token through which `email` passes this gate, or None."""
    if email in gate.users:
        return user_token(email)
    via_role = sorted(gate.roles & roles)
    if via_role:
        return role_token(project_key, via_role[0])
    via_group = sorted(gate.groups & memberships)
    return group_token(via_group[0]) if via_group else None

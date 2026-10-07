"""HTTP face of the Jira simulator.

Three groups of endpoints:

- `/rest/api/2/...`  read API shaped like Jira Cloud's REST API (v2, plain-text descriptions). Called with
  no user it answers as a service account that sees everything. With the header `X-Sim-User: <email>` it
  answers as that user: issues they may not see return the same 404 as issues that do not exist.
- `/sim/changes`, `/sim/webhooks`, `/sim/users/...`  change feed, webhook registration and a role lookup
  (simulator-only).
- `/sim/admin/...`  demo and test helpers: edit an issue, set its security level, revoke a role, add or
  remove hidden issues, reset. Never deploy these in front of real data. Set SIM_ADMIN_TOKEN to require
  `Authorization: Bearer <token>` on them.

Run: uvicorn simulators.jira.app:app --port 8102
"""
import hmac
import os
import re
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from simulators.common import Gone, Invalid, NotFound, deliver, norm_email, webhook_payloads
from simulators.jira.model import Actors, Issue, JiraSim
from simulators.jira.seed import seed_company_a

API = "/rest/api/2"
BROWSE = "BROWSE_PROJECTS"
MAX_CHECKED_ISSUES = 100
_JQL_PROJECT = re.compile(r"^\s*project\s*=\s*\"?([A-Za-z0-9]+)\"?\s*$")


class GrantsIn(BaseModel):
    roles: list[str] = Field(default_factory=list)
    groups: list[str] = Field(default_factory=list)
    users: list[str] = Field(default_factory=list)    # emails


class ActorsIn(BaseModel):
    users: list[str] = Field(default_factory=list)    # emails
    groups: list[str] = Field(default_factory=list)


class UserIn(BaseModel):
    email: str
    display_name: str | None = None


class ProjectIn(BaseModel):
    name: str | None = None


class IssueIn(BaseModel):
    project: str
    summary: str
    description: str = ""
    key: str | None = None            # omit to take the project's next number
    issue_type: str = "Task"
    status: str | None = None
    reporter: str | None = None
    security_level: str | None = None
    parent_key: str | None = None
    links: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    created_at: str | None = None
    updated_at: str | None = None
    version: str | None = None


class IssuePatch(BaseModel):
    summary: str | None = None
    description: str | None = None
    status: str | None = None
    links: list[str] | None = None
    version: str | None = None
    updated_at: str | None = None


class CommentIn(BaseModel):
    body: str
    author: str | None = None


class SecurityIn(BaseModel):
    level: str | None = None          # null clears it


class ProjectPermissionsIn(BaseModel):
    permissions: list[str]
    issues: list[str] = Field(default_factory=list)   # issue keys (real Jira takes numeric ids)
    projects: list[str] = Field(default_factory=list)


class BulkPermissionsIn(BaseModel):
    accountId: str
    projectPermissions: list[ProjectPermissionsIn]


class WebhookIn(BaseModel):
    url: str


class ResetIn(BaseModel):
    seed: str = "company_a"           # company_a | empty


def create_app(sim: JiraSim | None = None, *, admin_token: str | None = None,
               base_url: str = "http://localhost:8102") -> FastAPI:
    if sim is None:
        sim = JiraSim()
        seed_company_a(sim)
    app = FastAPI(title="Jira simulator", version="0.1")
    app.state.sim = sim
    app.state.deliver = deliver
    hooks: list[str] = []
    delivered = {"pos": len(sim.changelog.entries)}

    @app.exception_handler(NotFound)
    def _not_found(_, exc: NotFound) -> JSONResponse:
        return JSONResponse({"errorMessages": [str(exc)], "errors": {}}, status_code=404)

    @app.exception_handler(Gone)
    def _gone(_, exc: Gone) -> JSONResponse:
        return JSONResponse({"errorMessages": [str(exc)], "errors": {}}, status_code=410)

    @app.exception_handler(Invalid)
    def _invalid(_, exc: Invalid) -> JSONResponse:
        return JSONResponse({"errorMessages": [str(exc)], "errors": {}}, status_code=400)

    # ------------------------------------------------------------------ helpers
    def visible(viewer: str | None, key: str) -> Issue:
        """The issue, or the same NotFound whether it is missing or the viewer may not see it."""
        issue = sim.issues.get(key)
        if issue is None or (viewer is not None and not sim.can_view(viewer, key).allowed):
            raise NotFound("Issue does not exist or you do not have permission to see it.")
        return issue

    def user_json(email: str | None) -> dict | None:
        if not email:
            return None
        user = sim.users.get(norm_email(email))
        return {"accountId": user.account_id if user else None, "emailAddress": email,
                "displayName": user.display_name if user else email}

    def actors_json(actors: Actors) -> list[dict]:
        users = [{"type": "atlassian-user-role-actor", "displayName": u,
                  "actorUser": {"accountId": sim.users[u].account_id}} for u in sorted(actors.users)]
        groups = [{"type": "atlassian-group-role-actor", "displayName": g, "actorGroup": {"name": g}}
                  for g in sorted(actors.groups)]
        return users + groups

    def issue_json(issue: Issue) -> dict:
        acl = sim.acl(issue)
        level = sim.effective_level(issue)
        fields: dict = {
            "summary": issue.summary,
            "description": issue.description,
            "issuetype": {"name": issue.issue_type, "subtask": issue.parent_key is not None},
            "project": {"key": issue.project_key, "name": sim.projects[issue.project_key].name},
            "status": {"name": issue.status} if issue.status else None,
            "reporter": user_json(issue.reporter),
            "labels": issue.labels,
            "created": issue.created_at,
            "updated": issue.updated_at,
            "comment": {"comments": [{"author": user_json(c.author), "body": c.body, "created": c.created_at}
                                     for c in issue.comments], "total": len(issue.comments)},
            "issuelinks": [{"type": {"name": "Relates"}, "outwardIssue": {"key": link.removeprefix("jira:")}}
                           for link in issue.links if link.startswith("jira:")],
            "security": {"name": level} if level else None,
        }
        if issue.parent_key:
            fields["parent"] = {"key": issue.parent_key}
        return {
            "id": issue.key,
            "key": issue.key,
            "self": f"{base_url}{API}/issue/{issue.key}",
            "fields": fields,
            # Not part of Jira: what a real connector would have to assemble from several calls.
            "_simulator": {
                "version": issue.version,
                "links": issue.links,
                "browseUrl": f"{base_url}/browse/{issue.key}",
                "acl": {"tokens": acl["tokens"], "native": acl["native"], "snapshotHash": acl["snapshot_hash"]},
            },
        }

    def flush(background: BackgroundTasks) -> None:
        """Queue webhooks for everything logged since the last flush."""
        new = sim.changelog.entries[delivered["pos"]:]
        delivered["pos"] = len(sim.changelog.entries)
        if hooks and new:
            background.add_task(app.state.deliver, list(hooks), webhook_payloads(new, "issue", "key"))

    def require_admin(authorization: Annotated[str | None, Header()] = None) -> None:
        if admin_token and not hmac.compare_digest(authorization or "", f"Bearer {admin_token}"):
            raise HTTPException(401, "Simulator admin token required")

    Viewer = Annotated[str | None, Header(alias="X-Sim-User")]

    # ------------------------------------------------------------------ Jira-shaped read API
    @app.get(f"{API}/project")
    def list_projects() -> list[dict]:
        return [{"key": p.key, "name": p.name} for p in sorted(sim.projects.values(), key=lambda p: p.key)]

    @app.get(f"{API}/project/{{key}}")
    def get_project(key: str) -> dict:
        project = sim._project(key)
        return {"key": project.key, "name": project.name,
                "roles": {role: f"{base_url}{API}/project/{key}/role/{role}" for role in sorted(project.roles)}}

    @app.get(f"{API}/project/{{key}}/role/{{role}}")
    def get_role(key: str, role: str) -> dict:
        project = sim._project(key)
        if role not in project.roles:
            raise NotFound(f"No role {role!r} in project {key}")
        return {"name": role, "actors": actors_json(project.roles[role])}

    @app.get(f"{API}/project/{{key}}/securitylevel")
    def security_levels(key: str) -> dict:
        project = sim._project(key)
        return {"levels": [{"id": name, "name": name, "_simulator": {"members": project.levels[name].as_json()}}
                           for name in sorted(project.levels)]}

    @app.get(f"{API}/issue/{{key}}")
    def get_issue(key: str, viewer: Viewer = None) -> dict:
        return issue_json(visible(viewer, key))

    @app.get(f"{API}/search")
    def search(viewer: Viewer = None, jql: str | None = None,
               start_at: Annotated[int, Query(alias="startAt", ge=0)] = 0,
               max_results: Annotated[int, Query(alias="maxResults", ge=1, le=250)] = 50) -> dict:
        """Only `project = KEY` is understood. No jql lists everything the caller may see."""
        project = None
        if jql:
            match = _JQL_PROJECT.match(jql)
            if not match:
                raise Invalid("The simulator only understands JQL of the form: project = KEY")
            project = match.group(1).upper()
        keys = sorted(k for k, i in sim.issues.items() if project in (None, i.project_key))
        if viewer is not None:
            keys = [k for k in keys if sim.can_view(viewer, k).allowed]
        page = keys[start_at:start_at + max_results]
        return {"issues": [issue_json(sim.issues[k]) for k in page], "startAt": start_at,
                "maxResults": max_results, "total": len(keys)}

    @app.post(f"{API}/permissions/check")
    def permissions_check(body: BulkPermissionsIn) -> dict:
        """Live check for one user. A forbidden issue and a missing issue are both simply left out of `issues`."""
        user = sim.user_by_account(body.accountId)
        email = user.email if user else None
        results, details = [], {}
        for entry in body.projectPermissions:
            if entry.permissions != [BROWSE] or entry.projects:
                raise Invalid(f"The simulator only checks {BROWSE} on issues")
            if len(entry.issues) > MAX_CHECKED_ISSUES:
                raise Invalid(f"At most {MAX_CHECKED_ISSUES} issues per check")
            granted = []
            for key in entry.issues:
                decision = sim.can_view(email, key)
                issue = sim.issues.get(key)
                if issue is not None:
                    details[key] = {"aclSnapshotHash": sim.acl(issue)["snapshot_hash"]}
                if decision.allowed:
                    granted.append(key)
                    details[key]["grantPath"] = decision.grant_path
            results.append({"permission": BROWSE, "issues": granted, "projects": []})
        return {"projectPermissions": results, "_simulator": {"issues": details}}

    @app.get(f"{API}/user/search")
    def user_search(query: str) -> list[dict]:
        user = sim.users.get(norm_email(query))
        return [{**user_json(user.email), "accountType": "atlassian", "active": True}] if user else []

    @app.get(f"{API}/user/groups")
    def user_groups(account_id: Annotated[str, Query(alias="accountId")]) -> list[dict]:
        user = sim.user_by_account(account_id)
        if user is None:
            raise NotFound("No user found")
        return [{"name": g} for g in sim.groups_of(user.email)]

    # ------------------------------------------------------------------ simulator-only reads
    @app.get("/sim/users/{account_id}/roles")
    def user_roles(account_id: str) -> list[dict]:
        """Every project role the user fills, directly or through a group. Real Jira needs one call per project role."""
        user = sim.user_by_account(account_id)
        if user is None:
            raise NotFound("No user found")
        return [{"project": project, "role": role} for project, role in sim.roles_of(user.email)]

    @app.get("/sim/users/{account_id}/tokens")
    def user_tokens(account_id: str) -> list[str]:
        """The ACL tokens the user holds in Jira, as the connector reports them in PlatformIdentity.groups."""
        user = sim.user_by_account(account_id)
        if user is None:
            raise NotFound("No user found")
        return sorted(sim.identity_tokens(user.email))

    @app.get("/sim/changes")
    def changes(cursor: str | None = None, limit: Annotated[int, Query(ge=1, le=1000)] = 500) -> dict:
        entries, next_cursor, has_more = sim.changes(cursor, limit)
        return {"changes": [e.as_json() for e in entries], "next_cursor": next_cursor, "has_more": has_more}

    # ------------------------------------------------------------------ admin (demo and test helpers)
    admin = APIRouter(prefix="/sim", dependencies=[Depends(require_admin)])

    @admin.post("/webhooks")
    def add_webhook(body: WebhookIn) -> dict:
        if not body.url.startswith(("http://", "https://")):
            raise Invalid("Webhook url must be http or https")
        if body.url not in hooks:
            hooks.append(body.url)
        return {"webhooks": hooks}

    @admin.delete("/webhooks")
    def clear_webhooks() -> dict:
        hooks.clear()
        return {"webhooks": hooks}

    @admin.post("/admin/users")
    def add_user(body: UserIn) -> dict:
        return user_json(sim.add_user(body.email, body.display_name).email)

    @admin.put("/admin/groups/{group}/members/{email}")
    def add_member(group: str, email: str, background: BackgroundTasks) -> dict:
        changed = sim.add_member(group, email)
        flush(background)
        return {"group": group, "members": sorted(sim.groups[group]), "changed": changed}

    @admin.delete("/admin/groups/{group}/members/{email}")
    def remove_member(group: str, email: str, background: BackgroundTasks) -> dict:
        """Revoke: the user loses whatever this group granted, including roles the group fills."""
        changed = sim.remove_member(group, email)
        flush(background)
        return {"group": group, "members": sorted(sim.groups.get(group, set())), "changed": changed}

    @admin.put("/admin/projects/{key}")
    def put_project(key: str, body: ProjectIn) -> dict:
        project = sim.add_project(key, body.name)
        return {"key": project.key, "name": project.name}

    @admin.put("/admin/projects/{key}/permissions")
    def put_browse(key: str, body: GrantsIn, background: BackgroundTasks) -> dict:
        """Replace who holds Browse on the project."""
        sim.set_browse(key, body.roles, body.groups, body.users)
        flush(background)
        return {"key": key, "browse": sim.projects[key].browse.as_json()}

    @admin.put("/admin/projects/{key}/roles/{role}")
    def put_role(key: str, role: str, body: ActorsIn, background: BackgroundTasks) -> dict:
        """Replace who fills a project role."""
        sim.set_role_actors(key, role, body.users, body.groups)
        flush(background)
        return {"key": key, "role": role, "actors": sim.projects[key].roles[role].as_json()}

    @admin.delete("/admin/projects/{key}/roles/{role}/users/{email}")
    def remove_role_user(key: str, role: str, email: str, background: BackgroundTasks) -> dict:
        """Revoke: take one user out of a project role."""
        changed = sim.remove_role_actor(key, role, email)
        flush(background)
        return {"key": key, "role": role, "changed": changed}

    @admin.put("/admin/projects/{key}/securitylevels/{level}")
    def put_level(key: str, level: str, body: GrantsIn, background: BackgroundTasks) -> dict:
        """Define a security level or replace its members."""
        sim.set_security_level(key, level, body.roles, body.groups, body.users)
        flush(background)
        return {"key": key, "level": level, "members": sim.projects[key].levels[level].as_json()}

    @admin.post("/admin/issues")
    def create_issue(body: IssueIn, background: BackgroundTasks) -> dict:
        """Add an issue. With a security level, this is how Leak-CI plants a hidden document."""
        issue = sim.create_issue(
            body.project, body.summary, body.description, key=body.key, issue_type=body.issue_type,
            status=body.status, reporter=body.reporter, security_level=body.security_level,
            parent_key=body.parent_key, links=body.links, labels=body.labels, created_at=body.created_at,
            updated_at=body.updated_at, version=body.version,
        )
        flush(background)
        return issue_json(issue)

    @admin.put("/admin/issues/{key}")
    def edit_issue(key: str, body: IssuePatch, background: BackgroundTasks) -> dict:
        issue = sim.update_issue(key, summary=body.summary, description=body.description, status=body.status,
                                 links=body.links, version=body.version, updated_at=body.updated_at)
        flush(background)
        return issue_json(issue)

    @admin.post("/admin/issues/{key}/comments")
    def add_comment(key: str, body: CommentIn, background: BackgroundTasks) -> dict:
        issue = sim.add_comment(key, body.body, body.author)
        flush(background)
        return issue_json(issue)

    @admin.put("/admin/issues/{key}/security")
    def set_security(key: str, body: SecurityIn, background: BackgroundTasks) -> dict:
        """Restrict an issue to a security level, or clear it with null. Sub-tasks follow."""
        sim.set_issue_security(key, body.level)
        flush(background)
        return {"key": key, "security_level": sim.issues[key].security_level}

    @admin.delete("/admin/issues/{key}")
    def delete_issue(key: str, background: BackgroundTasks) -> dict:
        sim.delete_issue(key)
        flush(background)
        return {"deleted": key}

    @admin.post("/admin/reset")
    def reset(body: ResetIn | None = None) -> dict:
        seed = (body or ResetIn()).seed
        if seed not in ("company_a", "empty"):
            raise Invalid("seed must be 'company_a' or 'empty'")
        sim.clear()
        if seed == "company_a":
            seed_company_a(sim)
        delivered["pos"] = len(sim.changelog.entries)
        return {"seed": seed, "issues": len(sim.issues)}

    app.include_router(admin)
    return app


app = create_app(admin_token=os.environ.get("SIM_ADMIN_TOKEN") or None)

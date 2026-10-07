"""HTTP face of the Confluence simulator.

Three groups of endpoints:

- `/wiki/rest/api/...`  read API shaped like Confluence Cloud's REST API (v1). Called with no user it
  answers as a service account that sees everything. With the header `X-Sim-User: <email>` it answers
  as that user: pages they may not read return the same 404 as pages that do not exist.
- `/sim/changes`, `/sim/webhooks`  change feed and webhook registration (simulator-only; real Confluence
  has webhooks but no change feed).
- `/sim/admin/...`  demo and test helpers: edit a page, restrict it, revoke a permission, add or remove
  hidden pages, reset. Never deploy these in front of real data. Set SIM_ADMIN_TOKEN to require
  `Authorization: Bearer <token>` on them.

Run: uvicorn simulators.confluence.app:app --port 8101
     SIM_SEED=scale uvicorn simulators.confluence.app:app --port 8101    (Company A plus 12k generated pages, simulators/scale.py)
"""
import hmac
import os
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from simulators.common import Gone, Invalid, NotFound, deliver, webhook_payloads
from simulators.confluence.model import ConfluenceSim, Page, Principals
from simulators.confluence.seed import seed_company_a

API = "/wiki/rest/api"


class PrincipalsIn(BaseModel):
    groups: list[str] = Field(default_factory=list)
    users: list[str] = Field(default_factory=list)   # emails


class UserIn(BaseModel):
    email: str
    display_name: str | None = None


class SpaceIn(BaseModel):
    name: str | None = None


class PageIn(BaseModel):
    id: str
    space: str
    title: str
    body: str = ""
    parent_id: str | None = None
    author: str | None = None
    restrictions: PrincipalsIn = Field(default_factory=PrincipalsIn)
    links: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    created_at: str | None = None
    updated_at: str | None = None
    version: str | None = None


class PagePatch(BaseModel):
    title: str | None = None
    body: str | None = None
    links: list[str] | None = None
    version: str | None = None
    updated_at: str | None = None


class Subject(BaseModel):
    type: str
    identifier: str   # accountId


class PermissionCheck(BaseModel):
    subject: Subject
    operation: str = "read"


class WebhookIn(BaseModel):
    url: str


class ResetIn(BaseModel):
    seed: str = "company_a"   # company_a | scale | empty


SEEDS = ("company_a", "scale", "empty")


def _seed(sim: ConfluenceSim, seed: str) -> None:
    if seed not in SEEDS:
        raise ValueError(f"SIM_SEED must be one of {', '.join(SEEDS)}")
    if seed in ("company_a", "scale"):
        seed_company_a(sim)
    if seed == "scale":
        from simulators.scale import Scale, generate, seed_confluence
        seed_confluence(sim, generate(Scale.from_env()))


def create_app(sim: ConfluenceSim | None = None, *, admin_token: str | None = None,
               base_url: str = "http://localhost:8101") -> FastAPI:
    if sim is None:
        sim = ConfluenceSim()
        _seed(sim, os.environ.get("SIM_SEED") or "company_a")
    app = FastAPI(title="Confluence simulator", version="0.1")
    app.state.sim = sim
    app.state.deliver = deliver
    hooks: list[str] = []
    delivered = {"pos": len(sim.changelog.entries)}

    @app.exception_handler(NotFound)
    def _not_found(_, exc: NotFound) -> JSONResponse:
        return JSONResponse({"statusCode": 404, "message": str(exc)}, status_code=404)

    @app.exception_handler(Gone)
    def _gone(_, exc: Gone) -> JSONResponse:
        return JSONResponse({"statusCode": 410, "message": str(exc)}, status_code=410)

    @app.exception_handler(Invalid)
    def _invalid(_, exc: Invalid) -> JSONResponse:
        return JSONResponse({"statusCode": 400, "message": str(exc)}, status_code=400)

    # ------------------------------------------------------------------ helpers
    def visible(viewer: str | None, page_id: str) -> Page:
        """The page, or the same NotFound whether it is missing or the viewer may not read it."""
        page = sim.pages.get(page_id)
        if page is None or (viewer is not None and not sim.can_view(viewer, page_id).allowed):
            raise NotFound(f"No content found with id: {page_id}")
        return page

    def user_json(email: str) -> dict:
        user = sim.users.get(email)
        return {"accountId": user.account_id if user else None, "email": email,
                "displayName": user.display_name if user else email}

    def principals_json(p: Principals) -> dict:
        users = [user_json(u) for u in sorted(p.users)]
        groups = [{"type": "group", "name": g} for g in sorted(p.groups)]
        return {"user": {"results": users, "size": len(users)}, "group": {"results": groups, "size": len(groups)}}

    def page_json(page: Page) -> dict:
        acl = sim.acl(page)
        webui = f"/wiki/spaces/{page.space_key}/pages/{page.id}"
        return {
            "id": page.id,
            "type": "page",
            "status": "current",
            "title": page.title,
            "space": {"key": page.space_key, "name": sim.spaces[page.space_key].name},
            "ancestors": [{"id": a.id, "type": "page", "title": a.title} for a in sim.ancestors(page)],
            "history": {"createdDate": page.created_at, "createdBy": {"email": page.author}},
            "version": {"number": page.number, "when": page.updated_at},
            "body": {"storage": {"value": page.body, "representation": "storage"}},
            "metadata": {"labels": {"results": [{"name": name} for name in page.labels]}},
            "_links": {"base": base_url, "webui": webui, "self": f"{base_url}{API}/content/{page.id}"},
            # Not part of Confluence: what a real connector would have to assemble from several calls.
            "_simulator": {
                "version": page.version,
                "links": page.links,
                "acl": {"tokens": acl["tokens"], "native": acl["native"], "snapshotHash": acl["snapshot_hash"]},
            },
        }

    def flush(background: BackgroundTasks) -> None:
        """Queue webhooks for everything logged since the last flush."""
        new = sim.changelog.entries[delivered["pos"]:]
        delivered["pos"] = len(sim.changelog.entries)
        if hooks and new:
            background.add_task(app.state.deliver, list(hooks), webhook_payloads(new, "page", "id"))

    def require_admin(authorization: Annotated[str | None, Header()] = None) -> None:
        if admin_token and not hmac.compare_digest(authorization or "", f"Bearer {admin_token}"):
            raise HTTPException(401, "Simulator admin token required")

    Viewer = Annotated[str | None, Header(alias="X-Sim-User")]

    # ------------------------------------------------------------------ Confluence-shaped read API
    @app.get(f"{API}/space")
    def list_spaces() -> dict:
        results = [{"key": s.key, "name": s.name, "type": "global"} for s in sorted(sim.spaces.values(), key=lambda s: s.key)]
        return {"results": results, "size": len(results)}

    @app.get(f"{API}/space/{{key}}")
    def get_space(key: str) -> dict:
        if key not in sim.spaces:
            raise NotFound(f"No space with key: {key}")
        space = sim.spaces[key]
        permission = {"operation": {"operation": "read", "targetType": "space"}, "subjects": principals_json(space.view)}
        return {"key": space.key, "name": space.name, "type": "global", "permissions": [permission]}

    @app.get(f"{API}/content")
    def list_content(viewer: Viewer = None, space_key: Annotated[str | None, Query(alias="spaceKey")] = None,
                     start: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=250)] = 25) -> dict:
        ids = sorted(i for i, p in sim.pages.items() if space_key in (None, p.space_key))
        if viewer is not None:
            ids = [i for i in ids if sim.can_view(viewer, i).allowed]
        results = [page_json(sim.pages[i]) for i in ids[start:start + limit]]
        return {"results": results, "start": start, "limit": limit, "size": len(results)}

    @app.get(f"{API}/content/{{page_id}}")
    def get_content(page_id: str, viewer: Viewer = None) -> dict:
        return page_json(visible(viewer, page_id))

    @app.get(f"{API}/content/{{page_id}}/child/page")
    def child_pages(page_id: str, viewer: Viewer = None) -> dict:
        visible(viewer, page_id)
        kids = sorted((p for p in sim.pages.values() if p.parent_id == page_id), key=lambda p: p.id)
        if viewer is not None:
            kids = [p for p in kids if sim.can_view(viewer, p.id).allowed]
        return {"results": [page_json(p) for p in kids], "size": len(kids)}

    @app.get(f"{API}/content/{{page_id}}/restriction/byOperation/read")
    def read_restriction(page_id: str, viewer: Viewer = None) -> dict:
        """The page's own restriction only, as in Confluence. Inherited ones are on the ancestors."""
        page = visible(viewer, page_id)
        return {"operation": "read", "restrictions": principals_json(page.restrictions)}

    @app.post(f"{API}/content/{{page_id}}/permission/check")
    def permission_check(page_id: str, body: PermissionCheck) -> dict:
        """Live check for one user. A forbidden page and a missing page both answer hasPermission=false."""
        if body.subject.type != "user" or body.operation != "read":
            raise Invalid("The simulator only checks operation 'read' for subject type 'user'")
        user = sim.user_by_account(body.subject.identifier)
        decision = sim.can_view(user.email if user else None, page_id)
        extra: dict = {}
        page = sim.pages.get(page_id)
        if page is not None:
            extra = {"spaceKey": page.space_key, "aclSnapshotHash": sim.acl(page)["snapshot_hash"]}
        if decision.allowed:
            extra["grantPath"] = decision.grant_path
        return {"hasPermission": decision.allowed, "_simulator": extra}

    @app.get(f"{API}/user")
    def get_user(email: str) -> dict:
        user = sim.users.get(email.strip().lower())
        if user is None:
            raise NotFound("No user found")
        return {**user_json(user.email), "accountType": "atlassian"}

    @app.get(f"{API}/user/memberof")
    def member_of(account_id: Annotated[str, Query(alias="accountId")]) -> dict:
        user = sim.user_by_account(account_id)
        if user is None:
            raise NotFound("No user found")
        groups = [{"type": "group", "name": g} for g in sim.groups_of(user.email)]
        return {"results": groups, "size": len(groups)}

    # ------------------------------------------------------------------ change feed
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
        """Revoke: the user loses whatever this group granted."""
        changed = sim.remove_member(group, email)
        flush(background)
        return {"group": group, "members": sorted(sim.groups.get(group, set())), "changed": changed}

    @admin.put("/admin/spaces/{key}")
    def put_space(key: str, body: SpaceIn) -> dict:
        space = sim.add_space(key, body.name)
        return {"key": space.key, "name": space.name}

    @admin.put("/admin/spaces/{key}/permissions")
    def put_space_permissions(key: str, body: PrincipalsIn, background: BackgroundTasks) -> dict:
        """Replace who may view the space."""
        sim.set_space_view(key, body.groups, body.users)
        flush(background)
        return {"key": key, "view": sim.spaces[key].view.as_json()}

    @admin.post("/admin/pages")
    def create_page(body: PageIn, background: BackgroundTasks) -> dict:
        """Add a page. With restrictions, this is how Leak-CI plants a hidden document."""
        page = sim.create_page(
            body.id, body.space, body.title, body.body, parent_id=body.parent_id, author=body.author,
            groups=body.restrictions.groups, users=body.restrictions.users, links=body.links, labels=body.labels,
            created_at=body.created_at, updated_at=body.updated_at, version=body.version,
        )
        flush(background)
        return page_json(page)

    @admin.put("/admin/pages/{page_id}")
    def edit_page(page_id: str, body: PagePatch, background: BackgroundTasks) -> dict:
        page = sim.update_page(page_id, title=body.title, body=body.body, links=body.links,
                               version=body.version, updated_at=body.updated_at)
        flush(background)
        return page_json(page)

    @admin.delete("/admin/pages/{page_id}")
    def delete_page(page_id: str, background: BackgroundTasks) -> dict:
        sim.delete_page(page_id)
        flush(background)
        return {"deleted": page_id}

    @admin.put("/admin/pages/{page_id}/restrictions")
    def restrict_page(page_id: str, body: PrincipalsIn, background: BackgroundTasks) -> dict:
        """Replace the page's read restriction. Empty lists remove it. Applies to the whole subtree."""
        sim.set_restrictions(page_id, body.groups, body.users)
        flush(background)
        return {"id": page_id, "restrictions": sim.pages[page_id].restrictions.as_json()}

    @admin.post("/admin/reset")
    def reset(body: ResetIn | None = None) -> dict:
        seed = (body or ResetIn()).seed
        if seed not in SEEDS:
            raise Invalid("seed must be 'company_a', 'scale' or 'empty'")
        sim.clear()
        _seed(sim, seed)
        delivered["pos"] = len(sim.changelog.entries)
        return {"seed": seed, "pages": len(sim.pages)}

    app.include_router(admin)
    return app


app = create_app(admin_token=os.environ.get("SIM_ADMIN_TOKEN") or None)

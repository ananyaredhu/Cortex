# Simulators

Confluence and Jira are simulated because their free plans lack the permission features this project is about
([ADR-005](../docs/05-decisions/ADR-005-connectors.md)). A simulator is a small service with the platform's
permission rules, a REST API shaped like the real one, and admin endpoints for demos and tests.
Each one passes the same [contract tests](../connectors/tests/contract/) as every other connector.

| Simulator | Status | Port |
|---|---|---|
| [Confluence](confluence/) | Working | 8101 |
| Jira | Not started | 8102 |

## Confluence

```
make sim-confluence
# without make:  .venv\Scripts\uvicorn simulators.confluence.app:app --reload --port 8101
```
It starts seeded with the Confluence part of the Company A [fixtures](../fixtures/). State is in memory:
restarting, or `POST /sim/admin/reset`, returns to the seed.

Use it from Python through the connector, which implements the
[connector interface](../docs/02-contracts/connector-interface.md):
```python
from simulators.confluence import ConfluenceConnector

confluence = ConfluenceConnector.from_url("http://localhost:8101")
priya = confluence.resolve_identity("priya@companya.com")
confluence.check_access(priya, "confluence:SEC/q3-breach-report").allowed   # False
```
In tests, `simulators.confluence.testing.SeededConfluence()` gives a seeded connector with no server to start.

### Permission rules
Implemented in [`confluence/model.py`](confluence/model.py), with no I/O and no LLM:
1. A **space** grants view access to users and groups. The group `confluence-users` is everyone in the org (`public:org`).
2. A **page restriction** narrows access to the users and groups it lists. It never grants access to someone the space does not admit.
3. Restrictions are **inherited**: a reader must satisfy the restriction of every restricted ancestor and of the page itself.

`acl.tokens` on a document are the innermost restriction's principals, or the space's when nothing is restricted
(the narrowing rule in [acl-model](../docs/02-contracts/acl-model.md)). `acl.native` keeps the space permission
and the whole restriction chain. `check_access` is always evaluated live in the simulator.

### Read API (shaped like Confluence Cloud REST v1)
With no user it answers as a service account that sees everything. Send `X-Sim-User: <email>` to see what that
user sees: a page they may not read gives the same 404 as a page that does not exist.

| Endpoint | Returns |
|---|---|
| `GET /wiki/rest/api/space`, `GET /wiki/rest/api/space/{key}` | Spaces; one space with its view permission |
| `GET /wiki/rest/api/content?spaceKey=&start=&limit=` | Pages |
| `GET /wiki/rest/api/content/{id}` | One page: body, version, ancestors, and `_simulator` (version label, links, ACL evidence) |
| `GET /wiki/rest/api/content/{id}/child/page` | Child pages |
| `GET /wiki/rest/api/content/{id}/restriction/byOperation/read` | The page's own read restriction |
| `POST /wiki/rest/api/content/{id}/permission/check` | `{"hasPermission": bool}` for `{"subject": {"type": "user", "identifier": "<accountId>"}, "operation": "read"}` |
| `GET /wiki/rest/api/user?email=`, `GET /wiki/rest/api/user/memberof?accountId=` | A user; their groups |

Fields under `_simulator` are not part of Confluence. `expand` is ignored: pages always come fully expanded.

### Freshness
| Endpoint | Purpose |
|---|---|
| `GET /sim/changes?cursor=&limit=` | Change feed behind `list_changes`. No cursor = full crawl (every page as an `upsert`), then incremental `upsert`, `delete`, `acl_change` |
| `POST /sim/webhooks` `{"url": ...}` | Register a webhook. Each change is POSTed with `webhookEvent` = `page_created`, `page_updated`, `page_removed`, `content_permissions_updated`, `space_permissions_updated` or `group_membership_updated` |
| `DELETE /sim/webhooks` | Remove all webhooks |

Webhook delivery is best effort. The change feed is the fallback, so a lost webhook only adds lag.

### Admin endpoints (demo and test helpers, never in production)
| Action | Endpoint |
|---|---|
| Edit a document | `PUT /sim/admin/pages/{id}` `{"body": ..., "title": ...}` |
| Restrict a page (and its subtree) | `PUT /sim/admin/pages/{id}/restrictions` `{"groups": [...], "users": [emails]}`; empty lists lift it |
| Revoke a permission | `DELETE /sim/admin/groups/{group}/members/{email}`, or replace a space's viewers with `PUT /sim/admin/spaces/{key}/permissions` |
| Grant | `PUT /sim/admin/groups/{group}/members/{email}` |
| Add a hidden document (Leak-CI) | `POST /sim/admin/pages` `{"id", "space", "title", "body", "parent_id", "restrictions"}` |
| Remove a document | `DELETE /sim/admin/pages/{id}` (children move up, as in Confluence) |
| Add a user or space | `POST /sim/admin/users`, `PUT /sim/admin/spaces/{key}` |
| Reset | `POST /sim/admin/reset` `{"seed": "company_a"}` or `"empty"` |

Set the environment variable `SIM_ADMIN_TOKEN` to require `Authorization: Bearer <token>` on `/sim/admin/*`
and `/sim/webhooks`. Leave it unset for local development.

### Known limits
- Built against contract 0.1: a group membership change is reported as an `acl_change` on every page the group
  can reach. Contract 0.2 turns that into one `principal_change`.
- Not simulated yet: moving a page, edit restrictions, anonymous access, personal spaces, CQL search.
- The scale seed (12k+ pages) is a later task.

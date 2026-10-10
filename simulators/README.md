# Simulators

Confluence and Jira are simulated because their free plans lack the permission features this project is about
([ADR-005](../docs/05-decisions/ADR-005-connectors.md)). A simulator is a small service with the platform's
permission rules, a REST API shaped like the real one, and admin endpoints for demos and tests.
Each one passes the same [contract tests](../connectors/tests/contract/) as every other connector.

| Simulator | Status | Port |
|---|---|---|
| [Confluence](confluence/) | Working | 8101 |
| [Jira](jira/) | Working | 8102 |
| [Slack](slack/) | Read-only, for scale runs | 8103 |

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
| `GET /sim/changes?cursor=&limit=` | Change feed behind `list_changes`. No cursor = full crawl (every page as an `upsert`), then incremental `upsert`, `delete`, `acl_change`, `principal_change`. A cursor names its run of the log; after a restart or reset, an older one gets `410 Gone` and the connector raises `CursorExpired`, so ingestion crawls again on its own |
| `POST /sim/webhooks` `{"url": ...}` | Register a webhook. Each change is POSTed with `webhookEvent` = `page_created`, `page_updated`, `page_removed`, `content_permissions_updated`, `space_permissions_updated` or `group_membership_updated` |
| `DELETE /sim/webhooks` | Remove all webhooks |

Webhook delivery is best effort. The change feed is the fallback, so a lost webhook only adds lag.

The two kinds of permission change in the [connector interface](../docs/02-contracts/connector-interface.md) are kept apart:
- A page's own ACL changes (restriction set or lifted, space permission changed): one `acl_change` per affected page. Re-fetch it; its tokens changed.
- A person joins or leaves a group: one `principal_change` with `principal` = `user:<email>` and `token` = the group's token, and no `doc_id`. No page's tokens changed; drop cached identities and decisions for that person.

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

### Known limits (Confluence)
- Not simulated yet: moving a page, edit restrictions, anonymous access, personal spaces, CQL search.

## Jira

```
make sim-jira
# without make:  .venv\Scripts\uvicorn simulators.jira.app:app --reload --port 8102
```
Seeded with the Jira part of the Company A fixtures. Same conventions as Confluence: in-memory state,
`X-Sim-User` to read as a user, `SIM_ADMIN_TOKEN` to protect the admin endpoints.
```python
from simulators.jira import JiraConnector

jira = JiraConnector.from_url("http://localhost:8102")
priya = jira.resolve_identity("priya@companya.com")
jira.check_access(priya, "jira:SEC-17").allowed   # False: security level
```
In tests, `simulators.jira.testing.SeededJira()` gives a seeded connector with no server to start.

### Permission rules
Implemented in [`jira/model.py`](jira/model.py), with no I/O and no LLM:
1. A **project** has roles. A role's actors are users and groups, per project: `developer` in DBMIG is not `developer` in PAYINC.
2. The project grants **Browse** to roles, groups and users. The group `jira-users` is everyone in the org (`public:org`).
3. An **issue security level** lists its members (roles, groups, users). An issue at a level is visible only to people who hold Browse **and** are members of the level. A level never grants access on its own.
4. A **sub-task** takes its parent's security level and cannot set its own.

`acl.tokens` are the security level's members when the issue has a level, otherwise the Browse grants.
`acl.native` keeps the Browse grants, the level, its members and the parent a level was inherited from.
An identity's tokens are its groups plus `role:<project>:<role>` for every role it fills, directly or through a group.

One document is one issue: `body` is the description, then `Status: ...` when a status is set, then the comments.

### Read API (shaped like Jira Cloud REST v2)
| Endpoint | Returns |
|---|---|
| `GET /rest/api/2/project`, `GET /rest/api/2/project/{key}` | Projects; one project with its roles |
| `GET /rest/api/2/project/{key}/role/{role}` | The role's actors |
| `GET /rest/api/2/project/{key}/securitylevel` | The project's security levels |
| `GET /rest/api/2/issue/{key}` | One issue: fields, comments, links, and `_simulator` (version label, links, ACL evidence) |
| `GET /rest/api/2/search?jql=project = KEY&startAt=&maxResults=` | Issues. Only `project = KEY` is understood |
| `POST /rest/api/2/permissions/check` | Which of the listed issues the account may browse: `{"accountId": ..., "projectPermissions": [{"permissions": ["BROWSE_PROJECTS"], "issues": [keys]}]}` |
| `GET /rest/api/2/user/search?query=<email>`, `GET /rest/api/2/user/groups?accountId=` | A user; their groups |
| `GET /sim/users/{accountId}/roles`, `GET /sim/users/{accountId}/tokens` | Every project role the user fills; every ACL token they hold (simulator-only) |

Differences from real Jira: issues are addressed by key everywhere (real Jira's permission check takes numeric ids),
and descriptions are plain text.

### Freshness
`GET /sim/changes` and `POST /sim/webhooks` work as for Confluence. Webhook events: `jira:issue_created`,
`jira:issue_updated`, `jira:issue_deleted`, `comment_created`, `issue_security_updated`,
`issue_security_scheme_updated`, `project_permissions_updated`, `project_role_updated`, `group_membership_updated`.

A role or group membership change is a `principal_change`, one per token the person gained or lost. Leaving a group
that fills a project role costs two tokens, the group's and the role's, so it produces two entries.

### Admin endpoints (demo and test helpers, never in production)
| Action | Endpoint |
|---|---|
| Edit a document | `PUT /sim/admin/issues/{key}` `{"summary", "description", "status"}`; `POST /sim/admin/issues/{key}/comments` `{"body", "author"}` |
| Restrict an issue (and its sub-tasks) | `PUT /sim/admin/issues/{key}/security` `{"level": "security"}`; `null` clears it |
| Revoke a permission | `DELETE /sim/admin/projects/{key}/roles/{role}/users/{email}`, `DELETE /sim/admin/groups/{group}/members/{email}`, or replace Browse with `PUT /sim/admin/projects/{key}/permissions` |
| Grant | `PUT /sim/admin/projects/{key}/roles/{role}` `{"users": [emails], "groups": [...]}`, `PUT /sim/admin/groups/{group}/members/{email}` |
| Define a security level | `PUT /sim/admin/projects/{key}/securitylevels/{level}` `{"roles", "groups", "users"}` |
| Add a hidden document (Leak-CI) | `POST /sim/admin/issues` `{"project", "summary", "description", "security_level"}`, or with `"parent_key"` for a sub-task |
| Remove a document | `DELETE /sim/admin/issues/{key}` (its sub-tasks go too, as in Jira) |
| Add a user or project | `POST /sim/admin/users`, `PUT /sim/admin/projects/{key}` |
| Reset | `POST /sim/admin/reset` `{"seed": "company_a"}` or `"empty"` |

### Known limits (Jira)
- Not simulated: reporter and assignee as level members, permission schemes shared between projects,
  moving issues between projects, workflows, JQL beyond `project = KEY`.

## Slack (for scale runs)
```
SIM_SEED=scale .venv/Scripts/uvicorn simulators.slack.app:app --port 8103
```
The in-memory Slack that the Slack connector's tests use (`connectors/slack/fake.py`), served over HTTP:
`POST /api/<method>` answers the Web API methods the connector calls, and `GET /sim/identity-map` gives the identity
map a real workspace keeps in `identity-map.local.json`. The real connector runs against it unchanged
(`simulators/slack/connector.py`). Read-only: no admin endpoints, no events, and no token is checked, so never
expose it. Real Slack stays the source for the demo; this exists to test 200+ channels.

## Scale seed
`SIM_SEED=scale` on the Confluence and Slack simulators loads Company A plus a generated company
([`scale.py`](scale.py)): 400 people in 40 teams, 80 spaces with 12,000 nested pages (8% restricted subtrees), and
220 Slack channels (a quarter private) with about 1,760 threads and 3,200 replies. It is deterministic, and
`SCALE_PAGES`, `SCALE_CHANNELS` and `SCALE_PEOPLE` shrink it for a quick try. Priya, Dana and Maya join a few
generated teams and private channels; Jordan and Sam join none.

The permission shapes are kept simple (restrictions only narrow and are never nested), so the generator states each
document's tokens from its own spec. `connectors/ingestion/scale_run.py` ingests the seed into a separate
`brain_scale` database and checks the index against that spec.

First full run (7 Oct, fake embedder): about 12 documents/s for both sources (Confluence 17 min, Slack 2.3 min),
bounded by per-document writes to Postgres; every check passed. With bge-m3 the embedding dominates: 0.61 chunks/s
measured, about 8 hours for the corpus on this laptop's CPU, so run it overnight. Numbers in `docs/status/ws-a.md`. Run the scale Confluence simulator on another port
(e.g. 8111, with `CONFLUENCE_SIM_URL`) to keep the demo one on 8101 untouched.

## Hidden documents for Leak-CI
[`leakci.py`](leakci.py) plants, edits and removes documents an asker may not see, each with a unique canary string,
through the admin endpoints above. It is for C's metamorphic Leak-CI tests: ask, plant a hidden document on the same
topic, ingest, ask again, expect the same answer and never the canary.
```
python -m simulators.leakci plant confluence --topic "Q3 breach security incident report"     # own space
python -m simulators.leakci plant confluence --mode restricted --topic "..."                  # restricted page in ENG
python -m simulators.leakci plant jira --mode level --topic "..." --visible-to priya            # a control
python -m simulators.leakci plant confluence --topic "..." --visible-to dana --fact "..."       # a control stating a fact to ask for
python -m simulators.leakci verify <doc_id> --asker sam --index                                 # hidden, and indexed?
python -m simulators.leakci edit <doc_id> | remove <doc_id> | list | clear
```
Each planted document has its own holders group (empty unless `--visible-to`), so a control document never opens
another planted one. `verify` keeps a test from passing vacuously: the document must be hidden from the asker
(live `check_access`) and, with `--index`, be indexed with its canary and tokens the asker does not hold. From
Python: `LeakCI.from_env()`, or `LeakCI(confluence_client, jira_client)` in tests. Planted documents carry the label
`leakci`, so the tool keeps no state of its own. Everything prints JSON: doc IDs, canaries and persona names.

## Shared code
[`common.py`](common.py) holds what both simulators use: the change feed with its full crawl, webhook delivery,
and the simulators' error types. Both follow contract 0.2.

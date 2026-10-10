# HTTP API

version: 0.2 (additions for the UI, all optional; proposed by C, approved by A and B on 8 Oct and implemented by the Brain in `brain/api/app.py`)
Producer: Workstream B. Consumer: C (UI, console). The MCP server exposes the same pipeline: see [mcp-tools](mcp-tools.md).

Base path `/v1`. JSON. **Auth:** `Authorization: Bearer <JWT>` from the mock IdP on **every** request (validated per request: signature, expiry, audience). JWT claims: `sub`, `email`, `roles`, `aud`.

Errors: `{ "error": {"code": "...", "message": "..."} }`. Refusals are **not errors**: they return 200 with `refused: true` and the uniform refusal shape.

**Optional fields (0.2).** Fields marked *(0.2, optional)* may be absent or `null`. Clients must render without them; producers add them when ready. A response that has a field in one case must have it in every case of the same endpoint (refusal included), so the shape never depends on what exists.

**Roles.** `compliance` for the audit endpoints. `admin` endpoints accept `security-lead` or `compliance` in the Company A personas (there is no separate admin persona).

## `POST /idp/token` *(0.2, optional, demo only)*
The mock IdP. Served only when the Brain runs with `BRAIN_MOCK_IDP=1` and a `JWT_SIGNING_KEY` of at least 32 bytes; otherwise the route does not exist (404). Request: `{ "persona": "priya" }`. Response: `{ "access_token": "<JWT>", "token_type": "Bearer", "expires_in": 900, "persona": { "id", "display_name", "email", "roles" } }`. The token is HS256 with `sub`, `email`, `roles`, `name`, `aud`, `iss: "mock-idp"`, `iat`, `exp`. Unknown persona: 404. There is no password: this stands in for "Sign in with ..." and knows only the fictional Company A personas. A real IdP replaces this endpoint and nothing else.

**Development login.** `Bearer dev:<persona>` and the `/sim/*` helpers are served only when `BRAIN_DEV_AUTH=1` (off by default). With `BRAIN_ENV=production` the Brain refuses to start with dev login on.

## `POST /v1/ask`
Request: `{ "question": "...", "conversation_id": "optional", "skill_hint": "optional", "sources": ["jira", "slack"], "time_range": "any" }`
- `sources` *(0.2, optional)*: limit the search to these sources (`confluence`, `jira`, `slack`, `gdrive`). Absent or empty means all.
- `time_range` *(0.2, optional)*: `last_week`, `last_quarter` or `any` (default).

Response:
```json
{
  "request_id": "req_9f2c",
  "conversation_id": "c_1",
  "answer": "...",
  "claims": [{"text": "...", "citations": ["jira:DBMIG-142"]}],
  "citations": [{"doc_id": "jira:DBMIG-142", "title": "...", "url": "...", "source": "jira", "as_of": "2026-10-10T13:58:00Z",
                 "why_visible": ["role:DBMIG:developer"], "excerpt": "Database migration cutover is blocked ..."}],
  "refused": false,
  "abstained": false,
  "generator_unavailable": false,
  "freshness": {"oldest_source_as_of": "...", "stale_refetched": 0,
                "per_source": {"jira": {"last_sync": "2026-10-10T14:03:00Z", "status": "ok"}}},
  "skill": "status-and-blockers",
  "coverage": {"jira": {"searched": true, "shown": 2}, "slack": {"searched": true, "shown": 1},
               "confluence": {"searched": true, "shown": 0}, "gdrive": {"searched": false, "shown": 0}},
  "grounding": {"score": 0.96, "removed_claims": 0},
  "policy_version": "pol-0.3",
  "unavailable_sources": [],
  "clarify": null
}
```
Fields added in 0.2 (all optional):
- `citations[].excerpt`: up to 280 characters of the cited document, sanitized like the context packet. Shown as a quoted excerpt, never as the assistant's words.
- `freshness.per_source`: per source, the connector's `last_sync` and `status` (`ok` within the SLA, `stale` beyond it, `unavailable` if the source could not be read for this request).
- `coverage`: per source, whether it was searched and how many cited documents came from it. **Only what the asker may see is counted. No count of candidates, denied or filtered documents appears anywhere in this response** (a denied count is an existence signal: see [acl-model](acl-model.md)).
- `grounding`: share of claims verified against an allowed source (0 to 1), and how many unsupported claims the checker removed. `null` when refused.
- `policy_version`: the policy the PDP used for this request.
- `unavailable_sources`: sources that could not be read for this request (the answer comes from the others).
- `clarify`: `{"question": "...", "options": ["...", "..."]}` when the question is ambiguous between things the asker may see; then `answer` is empty and `refused` is false. Options are built only from allowed documents.

Refusal: `{ "refused": true, "answer": "I couldn't find anything you have access to about that.", "claims": [], "citations": [] }`, with every 0.2 field present in the same shape (`coverage` shows `shown: 0` for each searched source). Same shape and similar timing for forbidden and nonexistent content. No counts, no titles.

`generator_unavailable` *(0.2, optional)*: `true` only when sources the asker may see were found but the answer service failed (unreachable, throttled, or an unreadable reply). Then `answer` is the fixed message "The answer service is temporarily unavailable ...", `claims` and `citations` are empty, `refused` and `abstained` are `false`, and `grounding` is `null`. It is `false` in every other case, refusals included, so the field is always present. It can never be `true` for a refusal, because with no evidence the generator is not asked: a forbidden and a nonexistent document stay identical. `abstained` keeps its meaning: the model answered and nothing it could cite supports an answer.

`answer` is the verified claims rendered as text (`"Here is what I found:\n- ..."`), never the model's free-text prose.

## `POST /v1/ask/stream` *(0.2, optional)*
Same request as `/v1/ask`. Server-sent events, so the UI can show real pipeline progress instead of a timed animation:
- `event: stage`, `data: {"stage": "retrieve" | "authorize" | "verify_live" | "generate" | "check", "status": "start" | "done"}`
- `event: result`, `data:` the `/v1/ask` response.

All five stages are always sent, in that order, for every request (refusals included), so the stream does not reveal what exists.

## `GET /v1/conversations` *(0.2, optional)*
The caller's own conversations, newest first: `{ "conversations": [{"conversation_id": "c_1", "title": "<first question>", "last_asked_at": "..."}] }`. Titles are the caller's own question text. The list is rebuilt from the audit log (`ask` events carry `conversation_id`), so it survives a restart of the Brain.

## `GET /v1/conversations/{conversation_id}` *(0.2, optional)*
Reopens one of the caller's own conversations with every turn, oldest first, so the UI can show it again and continue it (pass the same `conversation_id` to `/ask`).
```json
{
  "conversation_id": "c_1", "title": "<first question>", "last_asked_at": "...",
  "turns": [
    {"request_id": "req_9f2c", "asked_at": "...", "question": "...", "skill": null,
     "answer": "...", "withheld": false,
     "citations": [{"doc_id": "jira:DBMIG-142", "title": "...", "url": "...", "source": "jira", "as_of": "...", "why_visible": ["user:priya@companya.com", "role:DBMIG:developer"]}],
     "refused": false, "abstained": false}
  ]
}
```
Rules:
- Someone else's conversation and a nonexistent one both answer `404 {"detail": "no such conversation"}`.
- An answer is a derived artifact ([acl-model](acl-model.md), labels): it is shown again only if the caller may still open **every** document it cited, by its stored label and a live check. Otherwise `answer` is `null`, `citations` is `[]` and `withheld` is `true`; the question stays. Nothing in a withheld turn names the document that was revoked.
- Turns carry what the log holds: no `claims`, `freshness`, `coverage` or `grounding`. The UI renders a reopened answer as plain text with its citations.

## `GET /v1/mywork`
Personalized home for the logged-in user: assigned issues, projects, recent pages, channels, suggested questions, pending stale-answer alerts. Everything is fetched through the PDP: nothing the user cannot see.
```json
{
  "user": {"display_name": "Priya", "roles": ["engineer"]},
  "issues": [{"doc_id": "jira:DBMIG-142", "title": "...", "status": "In Progress", "url": "..."}],
  "projects": ["jira:DBMIG"],
  "channels": ["slack:C_DBMIG"],
  "recent_pages": [{"doc_id": "confluence:PAY/runbook-payment-service", "title": "...", "updated_at": "...", "url": "..."}],
  "suggested_questions": ["..."],
  "alerts": []
}
```
`issues[].status` and the `url` fields are *(0.2, optional)*.

## `GET /v1/explain-access?doc_id=...`
Why can I see this? Returns `{"found": true, "proof_path": [...]}` for an allowed document. For a forbidden or nonexistent one, returns the uniform `{"found": false}`.

## `GET /v1/alerts`
Stale-answer alerts: answers the user received whose sources changed afterwards (`request_id`, `changed_doc`, `changed_at`, `summary`).
*(0.2, optional)* `question` (the user's own question, so the UI can ask it again) and `changed_title`. Alerts cover content changes to documents the user **may still see**; a document they have lost access to never produces an alert (it would reveal that it changed).

## Audit (compliance role only)
- `POST /v1/audit/query` with `{ "question": "..." }` (natural language) or `{ "filter": {user, space, from, to, decision} }` returns `{events, count}`, with timestamps, retrieved IDs and allow/deny decisions.
- *(0.2)* In returned events, `answer.text` is `null` with `answer.text_withheld: true` unless the **viewing** officer may see every document the answer cites; `answer.sha256` is always there. The filter's `space` matches allowed document ids only (denied ones are salted hashes by design). Every audit query is itself logged as `audit_query`.
- `GET /v1/audit/verify` returns `{ok, checked, checkpoints}` or `{ok:false, first_broken_seq, reason}`.
- `GET /v1/audit/time-travel?user=<email>&at=<ISO 8601 time>` *(0.2, implemented 10 Oct)*: what the person could open at `at`, and what changed since. Compliance role only; the query is itself logged. A bad `at` is 422.
  ```json
  {"user": "priya@companya.com", "at": "2026-10-10T14:00:00Z",
   "identity": {"known": true, "recorded_at": "2026-10-10T13:41:02.118Z", "first_recorded_at": "...", "tokens": ["user:priya@companya.com", "channel:C_AUTHPRIV"]},
   "could_see": [{"doc_id": "slack:C_DBMIG/thread-1", "title": "...", "url": "...", "source": "slack", "via": ["channel:C_DBMIG"]},
                 {"doc_id": "slack:C_AUTHPRIV/thread-1", "restricted": true}],
   "changed_since": {"lost": [{"doc_id": "slack:C_AUTHPRIV/thread-1", "restricted": true}], "gained": []}}
  ```
  A document shows its title and `via` (the tokens that opened it) only if the **viewing officer** may open it; otherwise `{doc_id, restricted: true}`, as in `replay`. `identity.known` is `false` when `at` is before the first time the Brain recorded the person's token set (the Brain's start, for anyone in the identity map): the answer is then empty rather than a guess. The document side is ingestion's bi-temporal `acl_snapshots`; the person side is the `identity_snapshot` events in the audit log. Documents deleted since are not listed.
- `GET /v1/audit/replay?request_id=...` *(0.2, optional)*: `{ "then": {"answer", "citations", "policy_version"}, "now": {"answer", "citations", "policy_version"}, "differences": [{"doc_id": "...", "change": "revoked" | "edited" | "deleted"}] }`. `then` is rebuilt from the logged document versions and policy, not from the live index. Titles and text appear only for documents the **viewing** officer may see; anything else is `{"doc_id": "...", "restricted": true}`.

## Admin and ops (admin role)
- `GET /v1/freshness` returns the freshness report Workstream A produces (`connectors/ingestion/freshness.py`, `summarize`): `{as_of, window_hours, sources: {<source>: {last_run_at, last_ok_at, last_error, freshness_lag_seconds: {count, p50, p95, max}, pipeline_lag_seconds: {...}, by_trigger: {...}}}}`.
- `GET /v1/leakci/latest` returns the latest Leak-CI and red-team scoreboard: `{as_of, cases, leaks}` and *(0.2, optional)* `suites: [{"name", "category", "passed", "failed", "last_run_at"}]`.
- `GET /v1/policy/versions` *(0.2, optional)*: `{versions: [{"policy_version", "author", "created_at", "pr_url"}], "active": "..."}`.
- `POST /v1/policy/evaluate` *(0.2, optional)* with `{"user": "<email>", "doc_id": "..."}`: `{"allowed": bool, "rule": "...", "proof_path": [...], "policy_version": "..."}`. Admin only, logged as `admin_view`, never available to other roles (it would otherwise reveal which documents exist).
- `GET /v1/health`.

## Demo and test helpers (simulators only, never in production)
Simulator admin endpoints (owned by A) to revoke a permission, restrict a page, edit a document, and add or remove hidden documents (for Leak-CI). Documented in `simulators/README.md`. The stub API has its own: `/sim/advance`, `/sim/reset`, `/sim/tamper`.

## Changelog
- 0.2, 10 Oct: `/audit/time-travel` implemented (response above).
- 0.2, 9 Oct: `/conversations/{conversation_id}` reopens a conversation (answers withheld when a cited document is no longer visible); `/conversations` is rebuilt from the audit log.
- 0.2 (proposed): optional UI fields on `/ask` (`excerpt`, `freshness.per_source`, `coverage`, `grounding`, `policy_version`, `unavailable_sources`, `clarify`) and its `sources` and `time_range` filters; `/ask/stream`; `/conversations`; `/audit/replay`; `/policy/versions` and `/policy/evaluate`; audit answer text withheld from officers who may not see its sources; `question` and `changed_title` on alerts, which only cover documents still visible; `/mywork` and `/freshness` shapes written down; roles for the admin endpoints. Explicit rule: no candidate or denied counts in any asker-facing response.
- 0.1: first draft.

# HTTP API

version: 0.2 (additions for the UI, all optional; proposed by C, approved by A and B on 8 Oct and implemented by the Brain in `brain/api/app.py`)
**0.3 is proposed (12 Oct, by C) and not yet approved or built.** Everything marked *(0.3, proposed)* below needs A and B to agree before any code: it adds the Brain's first write to a source (moving one's own Jira issue), ticket deadlines and priorities, conversation titles, and routines.
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
- `citations[].unverified` and `freshness.unverified_sources` *(fix F9)*: when the live version check of a cited document failed (a timeout or an error from the platform), the indexed copy is still served, but the citation says `unverified: true`, its source is listed in `freshness.unverified_sources` and its `freshness.per_source` status is `"unverified"` (besides `ok`, `stale`, `unavailable`). The UI shows a banner. A document that no longer exists is dropped, not served.
- `claims[].confidence` and `grounding.lower_confidence` *(fix F21)*: the grounding checker scores each claim against the documents it cites. A claim scoring at or above `GROUNDING_THRESHOLD` (0.05 for the default model) stands. One between `GROUNDING_FLOOR` (0.01) and the threshold is kept and carries `confidence: "lower"` (the rebuilt `answer` text adds "(lower confidence: a source supports this only weakly)"), and `grounding.lower_confidence` counts them. Below the floor it is dropped, as before. Clients should show the marker with the claim.
- `citations[].via_link_from`: the `doc_id` of the cited document whose stored link led to this one, or `null` for a direct search hit. A link never grants access: the target passed the same live permission check as any hit. A target the asker may not open, or that does not exist, is absent with no trace.
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

*(0.3, proposed)* **Titles.** Each conversation also carries `title_source`: `"question"`, `"generated"` or `"custom"`, and `title` is the first of these that exists: the name the caller gave it, else a generated title, else the first question (as in 0.2).
- **A generated title is written from the first question's text and nothing else.** After the first `/ask` of a conversation the Brain asks the generator model for a title of at most 60 characters, giving it only that question: not the answer, not a citation, not a document, not a later turn. The title is therefore made of the caller's own words, is derived from no source, and needs no ACL label ([acl-model](acl-model.md)). A title written from the answer would be a derived artifact and is not allowed here.
- It is generated for **every** conversation in the same way, whatever the outcome (answered, refused, abstained, unavailable), so a title says nothing about what exists. This does send a refused question's text to the model, which `/ask` itself does not; it is the caller's own text.
- It is generated after `/ask` has answered and never delays it. Until it exists, and if the model fails or times out, `title` is the first question. The model's output is untrusted: the Brain keeps one line of plain text, strips control characters and markup, and cuts it at 60 characters; the UI renders it as text.
- Titles are stored as `conversation_title` audit events ([audit-event-schema](audit-event-schema.md)), so they are rebuilt from the log like the list itself.

## `PATCH /v1/conversations/{conversation_id}` *(0.3, proposed)*
Rename one of the caller's own conversations: `{ "title": "DB migration blockers" }` (1 to 80 characters, one line). `{ "title": null }` drops the custom name and returns to the generated one. Returns the conversation as `/v1/conversations` lists it. Someone else's conversation and a nonexistent one both answer `404 {"detail": "no such conversation"}`; a bad title is `422`.

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

## `GET /v1/projects` *(0.2, 11 Oct, T7)*
The caller's projects from the project registry (`brain/projects/registry.json`): `{"projects": [{"project_id", "name", "member", "places"}]}`. `places` are the containers (`jira:DBMIG`, `slack:C_DBMIG`, `confluence:PAY`, `gdrive:folder-incidents`) the caller may open something in; a project none of whose places they may open is absent, and so is any place they cannot open (a private channel under a project they can see). `member` is true when the caller holds one of the project's member tokens, so seeing a project through public pages does not make someone part of it. Another person's projects are never returned. Also returned as `project_profile` in `/mywork`.

## `GET /v1/team` *(0.2, 11 Oct)*
Team focus. A team is a registry project the caller is a **member** of (`member: true` in `/v1/projects`); a project they can only see is not a team.
```json
{"teams": [{
  "project_id": "DBMIG", "name": "Database migration",
  "goal": "...", "start_date": "2026-09-14", "target_date": "2026-10-30", "leads": ["Maya"],
  "milestone": {"label": "Cutover", "date": "2026-10-27"},
  "sprint": {"name": "Sprint 42", "start_date": "2026-10-05", "end_date": "2026-10-16", "goal": "...", "total": 5, "done": 1},
  "places": ["jira:DBMIG", "slack:C_DBMIG"],
  "issues": {"total": 2, "done": 0, "by_status": [{"status": "In Progress", "count": 1}],
             "items": [{"doc_id": "jira:DBMIG-142", "title": "...", "status": "In Progress", "assignee": "Maya", "mine": false, "url": "..."}]},
  "updates": [{"doc_id": "slack:C_DBMIG/thread-1", "title": "...", "source": "slack", "place": "slack:C_DBMIG",
               "author": "Priya", "updated_at": "...", "url": "..."}]
}]}
```
Rules:
- **Every number is counted from documents the caller may open** (prefilter, then the live check), like everything in `/mywork`. `issues.total` is how many issues of the project they can open, not how many exist, so two members can see different totals and neither response hints at the other. Adding, editing or removing a document the caller cannot open changes nothing in their response (`evals/tests/test_team_leak.py`).
- `places` follows `/v1/projects`: a private channel under the project is absent for a member who is not in it.
- Someone who is a member of no project gets `{"teams": []}`, whether they can open nothing at all or see projects through public pages.
- `goal`, `start_date`, `target_date`, `milestone`, `leads` and the sprint's `name`, dates and `goal` come from the registry file, set by hand; any of them may be `null` (`sprint` and `milestone` as a whole). They are shown to every member of the project, including one who is only in its public channel, so the registry must say nothing there that only a restricted place holds.
- **The sprint's numbers follow a rule, not Jira's sprint field**, which is not ingested: an issue is in the sprint when it has a status and is still open, or is done and was last changed on or after `sprint.start_date`. `sprint.total` and `sprint.done` are counted from the issues the caller may open, like `issues`; a member who can open none gets `0` and `0`. The UI works out "day 5 of 10" (working days) and the timeline from the dates and the viewer's own date. `issues.items[].status` and `assignee` are read from the issue's text (`Status: <name>`, `Assignee: <display name>`), which is where the Jira connector writes them ([connector-interface](connector-interface.md)), and are `null` when the text has none; `done` counts Done, Closed and Resolved. `mine` is true when the assignee's name is the caller's display name, so two people with one display name would both be marked: it is a highlight, never an access decision. `updates` are the newest six documents in the project's places. `updates[].author` is a display name, or `null` when the Brain has none for that account.
- No model is involved, and nothing here is cached.
- Not in it, because the sources do not carry the data yet: Jira's own sprints, due dates and priorities, and on track / at risk. Also not in it: the list of a team's people, which would reveal who is in a private channel. Assignees appear only on issues the caller can open.
- *(0.3, proposed)* `issues.items[]` gains four optional fields, each `null` when the issue has none:
  - `updated_at`: when the issue last changed (the document's `updated_at`).
  - `due_date` (`YYYY-MM-DD`) and `priority` (the platform's name, e.g. `High`), from the Jira connector's `due` and `priority` fields ([connector-interface](connector-interface.md) 0.5). This replaces "due dates and priorities" in the line above once A ships them; Jira's own sprints and on track / at risk stay out.
  - `can_move`: `true` when the caller is the issue's assignee **by account** (the connector's `assignee_email` field equals the caller's canonical email). Unlike `mine`, it does not compare display names. It tells the UI which cards may be dragged; the decision itself is made again by `POST /v1/issues/move`.

## `POST /v1/issues/move` *(0.3, proposed)*
Move one's own Jira issue to another status: the Brain's only write to a source. Request: `{ "doc_id": "jira:DBMIG-150", "status": "Done" }`. Response: `{ "request_id": "req_...", "doc_id": "jira:DBMIG-150", "status": "Done", "updated_at": "...", "indexed": true }`.

**Jira holds the status; the Brain stores none.** The move is made in Jira first, and the index is then brought in line with Jira, so there is no second copy for a later sync to disagree with:
1. The policy plane decides (below). No model is involved at any step.
2. The Brain calls the Jira connector's `transition` as the caller ([connector-interface](connector-interface.md) 0.5). Jira applies its own workflow and permissions.
3. The connector returns the issue as Jira now holds it, and the Brain hands that document to ingestion's ordinary upsert path before answering, so `/v1/team` and `/v1/mywork` show the new status at once and the next sync finds the version it already has. If that re-index fails, the move still happened: the response says `indexed: false`, and the index catches up at the next sync (within the freshness SLA).

Rules, checked in this order:
- **May the caller open the issue?** Prefilter, then the live check, as for any read. If not, or the issue does not exist, or `doc_id` is not a Jira issue: `404 {"detail": "no such issue"}`. A forbidden and a nonexistent issue give the same body and similar timing, and this check comes before every other, so nothing below can reveal an issue the caller cannot open.
- **Is the caller its assignee?** By account, as for `can_move`. If not: `403 {"error": {"code": "not_assignee"}}`. This reveals nothing new: the caller can open the issue and already sees its assignee. An unassigned issue cannot be moved by anyone through Cortex.
- **Is `status` a status?** A string of 1 to 40 characters, matched without regard to case against the transitions Jira offers for that issue. Otherwise `422`.
- **Does Jira accept it?** If the workflow does not allow the move, or Jira denies it for its own reasons: `409 {"error": {"code": "transition_refused"}}`, and nothing has changed. If Jira cannot be reached: `503`, nothing has changed, and the Brain does not retry on its own (a retry could apply a move the person has since given up on).
- Moving an issue to the status it already has is `200` and changes nothing.

Limits:
- Status only. No other field, no comment, no assignment, no other source. Each further write needs its own contract change.
- **Never reachable from the LLM plane** (AGENTS rule 3): not an MCP tool, not a skill action, not something an answer or a routine can trigger. It is called by the UI on a person's own drag or menu choice.
- Every call is an `issue_transition` audit event, refused ones included ([audit-event-schema](audit-event-schema.md)).
- Security tests in `evals/tests/test_issue_move.py` before it is switched on: someone else's issue is `403` and unchanged in Jira; a forbidden and a nonexistent issue are indistinguishable; a person removed from the project between page load and drop gets the `404`; a move never changes what anyone else may see; every outcome is in the audit log and the chain verifies.

## `GET /v1/mywork`
Personalized home for the logged-in user: assigned issues, projects, recent pages, channels, suggested questions, pending stale-answer alerts. Everything is fetched through the PDP: nothing the user cannot see.
```json
{
  "user": {"display_name": "Priya", "roles": ["engineer"]},
  "issues": [{"doc_id": "jira:DBMIG-142", "title": "...", "status": "In Progress", "url": "..."}],
  "projects": ["jira:DBMIG"],
  "channels": ["slack:C_DBMIG"],
  "channel_names": {"slack:C_DBMIG": "#db-migration"},
  "recent_pages": [{"doc_id": "confluence:PAY/runbook-payment-service", "title": "...", "updated_at": "...", "url": "..."}],
  "suggested_questions": ["..."],
  "alerts": []
}
```
`issues[].status` and the `url` fields are *(0.2, optional)*.

*(0.3, proposed)* Each of `issues[]` gains the optional `updated_at`, `due_date`, `priority`, `assignee` (display name), `mine` and `can_move`, with the meanings they have in `/v1/team`, so My Work can show and filter by them without a second request. The response also gains the optional `routines` (the list `GET /v1/routines` returns).

## Routines *(0.3, proposed)*
A routine is a question its owner has the Brain ask again on a schedule, as themselves. Requirements, security rules and the build order are in [docs/routines.md](../routines.md); this is the HTTP surface. A run is an ordinary `/ask` made at run time with the owner's identity resolved fresh: nothing about access is stored with the routine.

| Call | Does |
|---|---|
| `GET /v1/routines` | The caller's routines: `{"routines": [{"routine_id", "name", "question", "sources", "schedule", "timezone", "status", "paused_reason", "next_run_at", "last_run"}]}`. `last_run` is `{"request_id", "conversation_id", "ran_at", "outcome", "unread"}` or `null`; `outcome` is `answered`, `abstained`, `unavailable` or `skipped`. |
| `POST /v1/routines` | Create: `{"name", "question", "sources"?, "schedule", "timezone"}`. `question` has the limits of `/ask`. `422` for a bad schedule or time zone; `409` at the limit of 5 active routines. |
| `PATCH /v1/routines/{routine_id}` | Change `name`, `question`, `sources`, `schedule` or `timezone`; `{"status": "paused" \| "active"}` pauses and resumes. |
| `DELETE /v1/routines/{routine_id}` | Delete. Past runs stay in the audit log. |
| `POST /v1/routines/{routine_id}/run` | Run now. Answers with the `/v1/ask` response; counts as a normal question. |
| `GET /v1/routines/{routine_id}/runs` | Past runs, newest first, in the shape of `last_run`. Each opens through `/v1/conversations/{conversation_id}`, which already withholds an answer whose sources the owner can no longer open. |

- `schedule` is fixed fields, never text a model reads: `{"frequency": "daily" | "weekdays" | "weekly", "weekday": 0-6 | null, "time": "09:00"}` (`weekday` is Monday = 0 and set only for `weekly`). `timezone` is an IANA name.
- **Private.** Another person's routine and a nonexistent one both answer `404 {"detail": "no such routine"}` with similar timing, on every call above. No list, count or admin view names who has routines; Admin shows totals of runs only.
- A run's stored answer is a derived artifact with ACL labels. `last_run` and `runs` carry no answer text: the text is read through the conversation, under its withholding rule.
- A run whose owner cannot be resolved (account removed, or a connector failing) is `skipped`. It is never run on an earlier identity.

## `GET /v1/explain-access?doc_id=...`
Why can I see this? Returns `{"found": true, "proof_path": [...]}` for an allowed document. For a forbidden or nonexistent one, returns the uniform `{"found": false}`.

## `GET /v1/alerts`
Stale-answer alerts: answers the user received whose sources changed afterwards (`request_id`, `changed_doc`, `changed_at`, `summary`).
*(0.2, optional)* `question` (the user's own question, so the UI can ask it again) and `changed_title`. Alerts cover content changes to documents the user **may still see**; a document they have lost access to never produces an alert (it would reveal that it changed).

## Audit (compliance role only)
- `POST /v1/audit/query` with `{ "question": "..." }` (natural language) or `{ "filter": {user, space, from, to, decision} }` returns `{events, count}`, with timestamps, retrieved IDs and allow/deny decisions.
- *(0.2)* In returned events, `answer.text` is `null` with `answer.text_withheld: true` unless the **viewing** officer may see every document the answer cites; `answer.sha256` is always there. The filter's `space` matches allowed document ids only (denied ones are salted hashes by design). Every audit query is itself logged as `audit_query`.
- `GET /v1/audit/verify` returns `{ok, checked, checkpoints, signer_pinned, head_anchored}` or `{ok:false, first_broken_seq, reason}`. When the log ends before the last signed head recorded outside the database (fix F4), `ok` is false and `reason` says the newest events were deleted. In development, `POST /sim/truncate?keep=N` deletes everything after the first N events to demonstrate it.
- `POST /v1/audit/ask` with `{ "question": "..." }` *(0.2, implemented 10 Oct)*: the audit agent. A question in words is turned into a validated plan (`kind`: `query`, `time_travel` or `verify`, with a `filter` of `user`, `space`, `from`, `to`, `decision`, `doc`), which the compliance role's audit code runs. Returns `{question, plan, clarify, summary, result}`: `plan` is also shown to the officer in words (`plan.understood`); `result` is the same object the structured endpoints return (`{events, count}`, the time-travel object, or `/verify`'s); `summary` is written by code from the result, not by a model. A question it cannot plan comes back with `plan: null`, a `clarify` message and no result. The planner sees only people, spaces and document titles, never audit events. Every question is logged once as `audit_query` with its plan in `detail.plan`. Compliance role only.
- *Refusals count as denials.* A question the Brain refused (nothing the person may open matched, so no document was ever checked) has no decision to find, so `decision: "denied"` also matches refused `ask` events, and a `doc` filter also matches refused questions that share at least two words with the document's title. "Who tried to read the Q3 breach report?" therefore finds the contractor's refused question. Summaries count them as "Refused questions".
- *Planner.* `plan.source` is `rules`, or `model:<name>` when the optional model planner was used. The rules run first; the model is asked only when they cannot read the question, and only if `AUDIT_PLANNER_BASE_URL`, `AUDIT_PLANNER_API_KEY` and `AUDIT_PLANNER_MODEL` are all set (any OpenAI-compatible `/chat/completions`, for example TokenHub with a Hunyuan model). The model sees the question, people's emails, space names and the time; never a document title and never an audit event. It names a document as a phrase (`doc_phrase`) that the Brain resolves locally. Its plan goes through the same validation.
- `space` filters accept `confluence:PAY` or a bare `PAY` (fix F14), case-insensitive. A new `doc` filter takes a document ID; it also matches denied decisions of that document through their salted hashes, so an auditor can find attempts on it. A `space` filter matches denied decisions the same way.
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
- `GET /v1/leakci/latest` returns the latest Leak-CI and red-team scoreboard: `{as_of, cases, leaks, status}` (*11 Oct, fix F7*: `status` is `ok`, `leaks` or `not_run`; with no run recorded `as_of` and `leaks` are `null` and `cases` is 0, never a "0 leaks" that no test produced) and *(0.2, optional)* `suites: [{"name", "category", "passed", "failed", "last_run_at"}]` and `target` (what the run asked: `fixture` or the URL of a served Brain). The Brain reads the file C's runner wrote last (`python -m evals.leakci`, default `evals/scoreboard/leakci-latest.json`, `LEAKCI_SCOREBOARD` to change it) and serves these summary fields only: the per-check notes, which name planted document ids and canaries, stay in the file. With no run recorded: `cases: 0, leaks: 0, suites: []`.
- `GET /v1/policy/versions` *(0.2, optional)*: `{versions: [{"policy_version", "author", "created_at", "pr_url"}], "active": "..."}`.
- `POST /v1/policy/evaluate` *(0.2, optional)* with `{"user": "<email>", "doc_id": "..."}`: `{"allowed": bool, "rule": "...", "proof_path": [...], "policy_version": "..."}`. Admin only, logged as `admin_view`, never available to other roles (it would otherwise reveal which documents exist).
- `GET /v1/health`.

## Demo and test helpers (simulators only, never in production)
Simulator admin endpoints (owned by A) to revoke a permission, restrict a page, edit a document, and add or remove hidden documents (for Leak-CI). Documented in `simulators/README.md`. The stub API has its own: `/sim/advance`, `/sim/reset`, `/sim/tamper`.

## Changelog
- 0.3 (proposed 12 Oct, not approved, not built): `POST /v1/issues/move`, the first write to a source, for the assignee's own Jira issue; `updated_at`, `due_date`, `priority` and `can_move` on issues in `/team`, and those plus `assignee` and `mine` in `/mywork`; `title_source`, generated titles (from the first question only) and `PATCH /v1/conversations/{id}`; the routines endpoints and `routines` in `/mywork`.
- 0.2, 11 Oct: `GET /v1/team` (Team focus) with `assignee` and `mine` per issue, and optional `goal`, `start_date`, `target_date`, `milestone` and `sprint` per project in the registry file.
- 0.2, 11 Oct: `GET /v1/projects` and `project_profile` in `/mywork` (T7).
- 0.2, 11 Oct (after the full real-stack run): `/mywork` gains the optional `channel_names` (`{channel id: name}`, taken from the titles of threads the caller may open; a real Slack ID such as `slack:C0C654ZPPB9` says nothing to a reader). `/alerts` and `alerts` in `/mywork` count only the caller's **latest** ask of each question: asking again supersedes the older answers, so they no longer raise an alert.
- 0.2, 11 Oct: `/leakci/latest` `status`, and `leaks: null` when no run exists; `/policy/versions` `history_kept: false` (fix F7).
- 0.2, 11 Oct: `citations[].unverified`, `freshness.unverified_sources` and the `unverified` source status (fix F9).
- 0.2, 11 Oct: `claims[].confidence: "lower"` and `grounding.lower_confidence` (fix F21).
- 0.2, 11 Oct: `skill` in the `/ask` response is the skill the Brain selected (`null` for the default procedure), not an echo of `skill_hint`. A follow-up in a `conversation_id` of the caller's own is understood from its earlier turns; someone else's or an unknown `conversation_id` behaves as no conversation.
- 0.2, 10 Oct: `/audit/time-travel` implemented (response above).
- 0.2, 10 Oct: `citations[].via_link_from` (link-edge expansion).
- 0.2, 10 Oct: `/leakci/latest` serves the scoreboard file Leak-CI writes, with an optional `target`; summary fields only.
- 0.2, 9 Oct: `/conversations/{conversation_id}` reopens a conversation (answers withheld when a cited document is no longer visible); `/conversations` is rebuilt from the audit log.
- 0.2 (proposed): optional UI fields on `/ask` (`excerpt`, `freshness.per_source`, `coverage`, `grounding`, `policy_version`, `unavailable_sources`, `clarify`) and its `sources` and `time_range` filters; `/ask/stream`; `/conversations`; `/audit/replay`; `/policy/versions` and `/policy/evaluate`; audit answer text withheld from officers who may not see its sources; `question` and `changed_title` on alerts, which only cover documents still visible; `/mywork` and `/freshness` shapes written down; roles for the admin endpoints. Explicit rule: no candidate or denied counts in any asker-facing response.
- 0.1: first draft.

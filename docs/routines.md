# Routines: requirement and implementation plan

Status: **proposed** (12 Oct). Built on the branch `ws-c/phase3-move-titles-routines` for review (steps 1 to 9 below, and the Admin totals of step 10); not merged, and off by default (`BRAIN_ROUTINES=1` starts the scheduler). Not built: F8 (the run's own date is not passed to the pipeline), a seeded demo routine, dragging a routine into Current focus, and the daily-brief skill. Step 1 of the plan, the contract changes, is written as proposals in `api.md` (0.3, "Routines"), `audit-event-schema.md` and `acl-model.md`, and waits for A and B to approve (AGENTS rule 1).

## 1. What it is

A routine is a question a person asks Cortex once and has it asked again on a schedule, as themselves.

> Priya sets a routine: every weekday at 09:00, "Summarise what needs my focus today." Each morning Cortex runs that question as Priya, and the answer is waiting for her on My Work and in Ask, with citations, like any other answer.

A routine run is an ordinary `/ask`: same pipeline, same permission checks at the moment it runs, same audit event. The only new things are the saved question, the schedule, and something that fires it.

## 2. Scope

**In (v1)**
- Create, edit, pause, resume and delete one's own routines.
- Schedules: every day, every weekday, or one day a week, at a chosen time in the person's time zone.
- A scheduler in the Brain that runs due routines whether or not the person has the UI open.
- "Run now" for trying a routine out.
- Results on My Work (latest run per routine) and in Ask (each run opens as a conversation).

**Out (v1)**
- Sending results anywhere outside Cortex (email, Slack, push). The answer stays inside the trust boundary; delivery is a separate decision.
- Routines that act (create tickets, post messages). The LLM plane has no write tools (AGENTS rule 3).
- Shared or team routines, and routines set by a manager for someone else.
- Schedules written in free text ("every other Tuesday"), intervals shorter than a day, and conditional triggers ("when DBMIG-142 changes"). Stale-answer alerts already cover the last one.
- Routines over MCP.

## 3. Requirements

### Functional
| ID | Requirement |
|---|---|
| F1 | A person can save a routine: a name, a question (the same limits as `/ask`), optional `sources`, a schedule and a time zone. |
| F2 | A routine can be created from the Ask composer, from an answer ("Make this a routine", prefilled with its question and sources), and from the Routines list. |
| F3 | The schedule is chosen from fixed fields (frequency, weekday, time, time zone), not parsed from text, so no model decides when something runs. |
| F4 | At the scheduled time the Brain asks the question as the owner. Each run is a new conversation, so yesterday's answer is not carried into today's as memory. |
| F5 | The owner sees, per routine: its schedule, next run, last run time and outcome (answered, abstained, unavailable, skipped), and a link that opens the last run in Ask. |
| F6 | My Work shows a "Routines" block with the latest run of each active routine, marked unread until opened. |
| F7 | A person can pause, resume, edit, delete and "Run now". A manual run counts as a normal question. |
| F8 | Words such as "today" and "this week" in the question mean the day of the run in the routine's time zone. |
| F9 | If the Brain was down at the scheduled time, the routine runs once when it comes back, if still on the same local day. Older misses are recorded as skipped, never run in bulk. |

### Security and trust
| ID | Requirement | Rule |
|---|---|---|
| S1 | **Access is decided at run time, never at creation time.** The scheduler builds the owner's identity fresh from the connectors for every run and stores no token, credential or token set with the routine. | Rules 2, 4 |
| S2 | If the owner's identity cannot be resolved (account removed, or a connector failing), the run is **skipped**, not run on an old identity. A missing token set is not treated as "no access", and not as "same as yesterday". | acl-model |
| S3 | A stored run is a derived artifact with ACL labels. It is shown again only if the owner may still open every document it cited (the existing conversation-reopen rule); otherwise it is withheld, without naming what changed. | Rule 6 |
| S4 | Routines are private. Another person's routine and a nonexistent one give the same `404`, and no list or count reveals that others have routines. | Rule 7 |
| S5 | Every run is an `ask` audit event marked as a routine run with its `routine_id`. Creating, editing, pausing and deleting a routine are audit events too. | audit schema |
| S6 | A run gives the same answer the owner would get by typing the question at that moment: no extra context, no wider retrieval, no cached answer from an earlier run. | Rule 5 |
| S7 | The scheduler is plain code in the Brain. It has no model in it, and the model never sees or changes a schedule. | Rule 2 |
| S8 | A routine's stored question is untrusted text, like any question. It goes through the same input handling as `/ask`. | Rule 3 |

### Limits and operations
| ID | Requirement |
|---|---|
| N1 | At most 5 active routines per person, and at most one scheduled run per routine per day. This caps model cost at 5 questions per person per day. |
| N2 | Runs due in the same minute are spread out (a queue with a small worker count), so 09:00 does not send every routine to the model at once. |
| N3 | A run that fails because the answer service is unavailable is retried once after a few minutes, then recorded as unavailable. Three unavailable runs in a row do not pause the routine; three skipped runs for an unresolvable owner do. |
| N4 | A routine nobody has opened for 14 days is paused, with a note on My Work. It costs nothing while paused. |
| N5 | Routines and their run history survive a restart of the Brain. |
| N6 | Admin shows how many runs happened, failed and were skipped in the last day, as totals only. |

## 4. How it works

### Data
One new table (the store from ADR-006; an in-memory version for fixture mode):

`routines`: `routine_id`, `owner` (email), `name`, `question`, `sources`, `schedule` (`{"frequency": "weekdays", "weekday": null, "time": "09:00"}`), `timezone` (IANA name), `status` (`active`, `paused`), `paused_reason`, `created_at`, `updated_at`, `next_run_at` (UTC), `last_opened_at`.

Run history is **not** a second copy of answers. A run is an `ask` event in the audit log carrying `routine_id`, and the list of runs is rebuilt from the log the way `/v1/conversations` already is. Skipped runs get a small `routine_skipped` event so the history has no silent gaps.

### API (now in api.md 0.3 as a proposal; that file is the contract)
| Call | Does |
|---|---|
| `GET /v1/routines` | The caller's routines, each with `next_run_at` and `last_run` (`{request_id, conversation_id, ran_at, outcome, unread}`). |
| `POST /v1/routines` | Create. `422` for a bad schedule or time zone; `409` at the limit of 5. |
| `PATCH /v1/routines/{id}` | Edit the name, question, sources, schedule or time zone; pause and resume. |
| `DELETE /v1/routines/{id}` | Delete. Past runs stay in the audit log. |
| `POST /v1/routines/{id}/run` | Run now; answers like `/ask` and can use `/ask/stream`'s stages. |
| `GET /v1/routines/{id}/runs` | Past runs, newest first; each opens through `/v1/conversations/{conversation_id}`, which already withholds revoked answers. |

`/v1/mywork` gains an optional `routines` list (the same shape as `GET /v1/routines`) so My Work needs no second request.

### Audit (now in audit-event-schema as a proposal; that file is the contract)
- `ask` events from a run: `actor.client: "routine"` and a top-level `routine_id`. `actor.user_id` is the owner.
- New `routine_change` event: `{action: created | updated | paused | resumed | deleted, routine_id}` with the question's text on create and update.
- New `routine_skipped` event: `{routine_id, reason: identity_unresolved | missed | limit}`.

### Scheduler
A loop in the Brain's runtime, next to the existing identity baseline sweep: every 30 seconds it takes routines whose `next_run_at` has passed, claims each one by moving `next_run_at` forward in the same transaction (so two Brain processes cannot both run it), and puts it on a small work queue. A worker resolves the owner's identity, builds the same principal sign-in would give them, and calls the pipeline's `ask`. `next_run_at` is computed from the schedule in the routine's time zone, so daylight-saving changes keep 09:00 at 09:00.

### UI
- **Ask:** a clock button in the composer and "Make this a routine" on an answer open one small form (name, frequency, time, time zone defaulting to the browser's).
- **Routines list:** a section reachable from the sidebar: each routine with its schedule, next run, last outcome, and pause, edit, delete, run now.
- **My Work:** a "Routines" block above "My tickets", one line per routine with the first lines of the latest answer and an unread dot. A routine card can be dragged into Current focus like any other row.
- A run opened in Ask looks like any reopened conversation, with a line saying which routine produced it and when.

## 5. Decisions needed

1. **"Due today" cannot be answered yet.** Jira due dates and priorities are not ingested (api.md, `/v1/team`: "not in it"). Today the example routine would summarise open tickets and what changed recently, and the checker would drop any claim about a due date. Options: (a) ship with the wording "what needs my focus today" and say so in the demo, (b) have Workstream A add `Due: <date>` to the Jira connector's text first. Recommended: (a) now, (b) as a follow-up. **Decided 12 Oct: (b).** Due dates and priorities are in the proposed connector-interface 0.5 (`Due:` and `Priority:` lines and fields) and must land by Thu 15 Oct.
2. **Server-side scheduler or browser-only.** A browser-only version (the UI fires the question when it is open after 09:00) needs no backend work and no new trust question, but it is not really a routine: nothing runs unless the tab is open. Recommended: server-side, as specified above. The browser-only version is a fallback if time runs out before the freeze on Thu 15 Oct. **Decided 12 Oct: server-side.**
3. **A "daily brief" skill.** The plain pipeline answers one question well; a brief over tickets, threads and pages reads better with its own skill (skills-format). Recommended: add it after the scheduler works, since routines are useful without it.
4. **Who may see that a routine exists.** Compliance sees runs and changes in the audit log, like any question. Recommended: no other visibility, including for managers.

## 6. Implementation plan

Order matters: contracts, then the Brain, then the UI on top of a stub. Owners follow the layout in AGENTS.md.

| # | Step | Owner | Size |
|---|---|---|---|
| 1 | **Contract PR:** the routines section in `api.md` (0.3), the three audit changes in `audit-event-schema.md`, a line in `acl-model.md` naming routine runs as labelled derived artifacts, and drift rows for the new screens. Reviewed by B and C before any code. | B + C | S |
| 2 | **Schedule maths:** a pure module (`brain/routines/schedule.py`) that turns a schedule and time zone into the next run time, with tests for weekdays, week boundaries, daylight-saving changes and a bad time zone. | B | S |
| 3 | **Store:** the `routines` table and its in-memory twin, with the per-person limit and the claim-and-advance operation; tests for the limit and for two claimers racing. | B | M |
| 4 | **Endpoints:** CRUD and "run now" in `brain/api/app.py`, owner checks returning the uniform `404`, `routine_change` events. Stub versions in `brain/stub_api` so the UI can start. | B | M |
| 5 | **Runner:** resolve the owner fresh, build the principal, call the pipeline with `client: "routine"` and the `routine_id`, one new conversation per run, the retry, and `routine_skipped`. Tested with a fake clock, no real waiting. | B | M |
| 6 | **Scheduler loop:** start and stop with the runtime, the 30-second tick, the work queue, catch-up on start (F9), auto-pause (N3, N4). Off by default behind `BRAIN_ROUTINES=1` until step 9 passes. | B | M |
| 7 | **Run history and My Work:** `GET /v1/routines/{id}/runs` rebuilt from the log, `last_run` and `unread`, and `routines` in `/v1/mywork`. | B | S |
| 8 | **UI:** the form, the Routines list, the My Work block and the "from a routine" line in Ask, built against the stub from step 4; unit tests and an axe pass on the new screens. | C | L |
| 9 | **Security tests in `evals/`** (below). The feature flag stays off until they pass on the fixture and the simulators. | C | M |
| 10 | **Demo:** a seeded routine for Priya, a "run now" moment in the scenario script (`docs/04-scenarios.md`), and the Admin totals (N6). | C | S |
| 11 | *Later:* the daily-brief skill, Jira due dates (decision 1), delivery outside Cortex. | B, A | — |

Sizes: S is a few hours, M about half a day, L about a day. Steps 2 to 4 and the UI's stub work can run in parallel once step 1 is agreed.

### Tests that must exist before it is switched on
- **Revoked before a run:** a routine cites a document; the owner loses access; the next run does not use, cite or mention it, and the response is the same as if the document had never existed.
- **Revoked after a run:** yesterday's stored run cited a document the owner can no longer open; the run is withheld in the list, on My Work and in Ask, and its preview text is gone too.
- **Hidden documents change nothing:** adding, editing or removing a document the owner cannot open leaves a routine's run unchanged (the Leak-CI add/remove test, applied to routine runs).
- **Other people's routines:** reading, editing, running or deleting someone else's `routine_id` gives the same `404` and timing as an unknown id.
- **Removed owner:** the run is skipped and logged, and nothing is sent to the model.
- **Connector down:** the run is skipped or marked unavailable; it does not run on an older identity.
- **Audit:** every run and every change appears in the log with the owner as actor, and the hash chain still verifies.
- **Limits:** the sixth routine is refused; a routine cannot run twice for one scheduled time with two Brain processes.

## 7. Done means
Priya creates the routine from an answer in under a minute; with the UI closed, the Brain runs it at the set time; the result is on My Work when she next opens Cortex and opens in Ask with its citations; Jordan can find the run in the audit console as Priya's question, marked as a routine; and every test above passes.

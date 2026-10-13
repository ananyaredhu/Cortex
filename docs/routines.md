# Routines

Routines are off by default: `BRAIN_ROUTINES=1` starts the scheduler; the endpoints and "Run now" work either way. The contract changes are in `api.md` (0.3, "Routines"), `audit-event-schema.md` and `acl-model.md`. Not built: the run's own date is not passed to the pipeline (F8), a seeded demo routine, dragging a routine into Current focus, and the daily-brief skill.

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

### Added 13 Oct (branch `ws-c/routines-run-state`)
| ID | Requirement |
|---|---|
| F10 | A run that is being answered says so wherever the routine is named: a "Running now" label and a moving bar on its card, a dot beside Routines in the sidebar, and on My Work. "Run now" is not offered while it runs. The routine carries `running` for this (api.md). |
| F11 | Each routine has a history page (`/routines/{id}` in the UI): every run, newest first, skipped ones included, with the chosen run's answer beside the list and a link that opens it in Ask. The answer is read through the run's conversation, so S3 holds there unchanged. |
| F12 | A run opened in Ask has a filled Trust panel: the sources searched, freshness, grounding and policy version recorded when it ran, marked as being from that time. |

## 4. How it works

### Data
One new table (the store from ADR-006; an in-memory version for fixture mode):

`routines`: `routine_id`, `owner` (email), `name`, `question`, `sources`, `schedule` (`{"frequency": "weekdays", "weekday": null, "time": "09:00"}`), `timezone` (IANA name), `status` (`active`, `paused`), `paused_reason`, `created_at`, `updated_at`, `next_run_at` (UTC), `last_opened_at`.

Run history is **not** a second copy of answers. A run is an `ask` event in the audit log carrying `routine_id`, and the list of runs is rebuilt from the log the way `/v1/conversations` already is. Skipped runs get a small `routine_skipped` event so the history has no silent gaps.

### API (api.md 0.3 is the contract)
| Call | Does |
|---|---|
| `GET /v1/routines` | The caller's routines, each with `next_run_at` and `last_run` (`{request_id, conversation_id, ran_at, outcome, unread}`). |
| `POST /v1/routines` | Create. `422` for a bad schedule or time zone; `409` at the limit of 5. |
| `PATCH /v1/routines/{id}` | Edit the name, question, sources, schedule or time zone; pause and resume. |
| `DELETE /v1/routines/{id}` | Delete. Past runs stay in the audit log. |
| `POST /v1/routines/{id}/run` | Run now; answers like `/ask` and can use `/ask/stream`'s stages. |
| `GET /v1/routines/{id}/runs` | Past runs, newest first; each opens through `/v1/conversations/{conversation_id}`, which already withholds revoked answers. |

`/v1/mywork` gains an optional `routines` list (the same shape as `GET /v1/routines`) so My Work needs no second request.

### Audit (audit-event-schema.md is the contract)
- `ask` events from a run: `actor.client: "routine"` and a top-level `routine_id`. `actor.user_id` is the owner.
- New `routine_change` event: `{action: created | updated | paused | resumed | deleted, routine_id}` with the question's text on create and update.
- New `routine_skipped` event: `{routine_id, reason: identity_unresolved | missed | limit}`.

### Scheduler
A loop in the Brain's runtime, next to the existing identity baseline sweep: every 30 seconds it takes routines whose `next_run_at` has passed, claims each one by moving `next_run_at` forward in the same transaction (so two Brain processes cannot both run it), and puts it on a small work queue. A worker resolves the owner's identity, builds the same principal sign-in would give them, and calls the pipeline's `ask`. `next_run_at` is computed from the schedule in the routine's time zone, so daylight-saving changes keep 09:00 at 09:00.

### UI
- **Ask:** a clock button in the composer and "Make this a routine" on an answer open one small form (name, frequency, time, time zone defaulting to the browser's).
- **Routines list:** a section reachable from the sidebar, in two tabs. *Yours*: each routine with its schedule, next run, last outcome, and pause, edit, delete, run now; with none yet, a prompt to create the first one or start from a template. *Templates*: eight starting points (morning brief, stand-up prep, week ahead, weekly wrap-up, blocker watch, decision log, incident follow-ups, design discussions) in `ui/src/routines/templates.ts`, each run against the real Brain on 12 Oct. The first four ask about "my" tickets and rely on the `my-work` skill (skills-format 0.3). Two were dropped because nothing can answer them yet: pages changed in the last week (no search by date) and threads the person has not replied to (no lookup of who was asked). A template only fills the form; every field can be changed before saving, and what is saved is an ordinary routine. Templates read and report, never act (see "Out").
- **Instructions, not a question.** The form's text box is labelled "Instructions" and takes several steps ("1. List my open tickets. 2. Say what changed."). It is still the routine's `question` on the wire and in the audit log, and a run is still one ordinary `/ask` (S6, S8): the wording changed, the contract did not.
- **My Work:** a "Routines" block above "My tickets", one line per routine with the first lines of the latest answer and an unread dot. A routine card can be dragged into Current focus like any other row.
- A run opened in Ask looks like any reopened conversation, with a line saying which routine produced it and when, and a link to all of that routine's runs.
- **Routine history:** the routine's name in the list, and "History", open its runs and their answers on one page (F11).

## 5. Tests that must exist before it is switched on
- **Revoked before a run:** a routine cites a document; the owner loses access; the next run does not use, cite or mention it, and the response is the same as if the document had never existed.
- **Revoked after a run:** yesterday's stored run cited a document the owner can no longer open; the run is withheld in the list, on My Work and in Ask, and its preview text is gone too.
- **Hidden documents change nothing:** adding, editing or removing a document the owner cannot open leaves a routine's run unchanged (the Leak-CI add/remove test, applied to routine runs).
- **Other people's routines:** reading, editing, running or deleting someone else's `routine_id` gives the same `404` and timing as an unknown id.
- **Removed owner:** the run is skipped and logged, and nothing is sent to the model.
- **Connector down:** the run is skipped or marked unavailable; it does not run on an older identity.
- **Audit:** every run and every change appears in the log with the owner as actor, and the hash chain still verifies.
- **Limits:** the sixth routine is refused; a routine cannot run twice for one scheduled time with two Brain processes.

## 6. Acceptance
Priya creates the routine from an answer in under a minute; with the UI closed, the Brain runs it at the set time; the result is on My Work when she next opens Cortex and opens in Ask with its citations; Jordan can find the run in the audit console as Priya's question, marked as a routine; and every test above passes.

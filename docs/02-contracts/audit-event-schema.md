# Audit event schema

version: 0.2 (0.2, 14 Oct: the action events, ADR-010; not built)
Producer: the Brain (audit service). Consumers: the console, the evals and the audit agent.

The log must be **tamper-evident**, **complete** (who, what, retrieved IDs, final answer, time, allow/deny per document) and **queryable**.

## Event
```json
{
  "seq": 1042,
  "ts": "2026-10-10T14:05:11.482Z",
  "request_id": "req_9f2c",
  "event_type": "ask",
  "conversation_id": "c_1",
  "actor": {"user_id": "priya@companya.com", "roles": ["engineer"], "client": "ui"},
  "query": {"text": "What's the status of the DB migration ...", "skill": "status-and-blockers", "skill_version": 1,
            "skill_sha256": "<hash of the skill folder>", "rewritten": null},
  "decisions": [
    {
      "doc_id": "jira:DBMIG-142",
      "allowed": true,
      "proof_path": ["user:priya@companya.com", "role:DBMIG:developer"],
      "acl_snapshot_hash": "sha256:...",
      "policy_version": "pol-0.3",
      "doc_version": "2026-10-10T13:58:00Z",
      "jit_checked": true
    },
    {
      "doc_id_hash": "sha256:salted...",
      "allowed": false,
      "reason": "no_access",
      "acl_snapshot_hash": "sha256:...",
      "policy_version": "pol-0.3"
    }
  ],
  "answer": {
    "text": "...",
    "sha256": "sha256:...",
    "citations": ["jira:DBMIG-142", "slack:C123/1728..."],
    "refused": false,
    "abstained": false,
    "unavailable": false,
    "acl_label": ["user:priya@companya.com"]
  },
  "links_followed": [{"doc_id": "slack:C_DBMIG/thread-1", "via_link_from": "jira:DBMIG-142"}],
  "checks": {"deterministic": "pass", "generator": "ok", "grounding_model": "pass", "leak_scan": "clean", "leak_scan_docs": 14},
  "models": {"generator": "deepseek-v3", "checker": "minicheck-...", "embedding": "bge-m3@1"},
  "latency_ms": 2310,
  "prev_hash": "sha256:...",
  "hash": "sha256:..."
}
```
Event types: `ask`, `search`, `mcp_call`, `audit_query`, `admin_view`, `acl_change_observed`, `alert_sent`, `checkpoint`.

## Hash chain
- `hash = SHA256( canonical_json(event without "hash") )`, where the canonical form includes `prev_hash`.
- `prev_hash` is the previous event's `hash`. The first event uses a fixed genesis value.
- Every N events (e.g. 100) write a `checkpoint` event signed with an **Ed25519** key (`AUDIT_SIGNING_KEY`). The public key is published so auditors can verify.
- Any edit, deletion, insertion or reordering breaks the chain at that point.

## Denied documents
Denied `doc_id`s are stored **salted-hashed**, so an auditor can see that attempts happened without the log becoming a leak. Allowed decisions store the plain `doc_id`.

## Identity snapshots
When a person's token set (`user:<email>` plus every group, channel and role from the connectors) differs from the last one written, the Brain appends `{"event_type": "identity_snapshot", "actor": {"user_id": "system:identity", "client": "brain"}, "identity": {"user": "<email>", "tokens": [...], "sources": [...]}}`. It happens for every known person (the identity map, or the fixture personas, plus anyone who has asked) when the Brain starts and every `baseline_interval_s` (1800 s, `BRAIN_BASELINE_INTERVAL_S`) after, at a person's next question, and eagerly when ingestion's outbox reports a `principal_change` for them. History therefore starts when the Brain does, whether or not a person ever asks. Nothing is written while a connector fails (missing tokens are not revoked ones). Time-travel queries read the last snapshot at or before the asked time.

## Replayability
Each decision stores `acl_snapshot_hash`, `policy_version` and `doc_version` so an auditor can re-evaluate "was this decision correct at that time?". Together with ACL snapshots stored with validity intervals (bi-temporal), this supports time-travel queries ("what could jdoe see on 12 Oct?").

## `/verify`
`GET /v1/audit/verify` returns `{ok: true, checked: N, checkpoints: M, signer_pinned, head_anchored}` or `{ok: false, first_broken_seq: K, reason: "..."}`. Must be fast enough to demo live.

Two checks. The chain: every event's hash and `prev_hash`, and every checkpoint's signature. And, when an anchor is configured (`AUDIT_HEAD_PATH`, required in production), **the log against the last signed head the anchor recorded** (fix F4): on every append the store signs `seq:hash` of the new head and appends it to a file outside the database. A log that ends before that head ("the newest events were deleted"), an event at that position that differs ("history was replaced"), a head whose signature does not verify, or events with no head on record all report `ok: false`. An anchor that is behind the log is fine: a crash between the commit and the write can leave it so. `head_anchored: false` means nothing outside the database was compared. `AUDIT_HEAD_ADOPT=1` records the current head once, for an anchor added to an existing log.

## Querying
Audit queries (natural language or structured) are read-only and run **under RBAC**: only the compliance role can query other users' history. Querying the audit log is itself logged (`audit_query`). Sensitive lookups may require a two-person approval (stretch).

## Conversations
`ask` events carry `conversation_id` (the one returned by `/ask`). The Brain keeps no other record of conversations: `/v1/conversations` and `/v1/conversations/{id}` are rebuilt from these events, under the same ownership rule (only the asker's own events), so history survives a restart.

## Events added with api.md 0.3 (12 Oct)
Four new event types and one new value, all in the hash chain like any other event.

**`issue_transition`**: every call to `POST /v1/issues/move`, whatever its outcome.
```json
{"event_type": "issue_transition", "request_id": "req_...",
 "actor": {"user_id": "priya@companya.com", "roles": ["engineer"], "client": "ui"},
 "decisions": [{"doc_id": "jira:DBMIG-150", "allowed": true, "proof_path": ["user:priya@companya.com", "role:DBMIG:developer"],
                "acl_snapshot_hash": "sha256:...", "policy_version": "pol-0.3", "doc_version": "...", "jit_checked": true}],
 "transition": {"from": "In Progress", "to": "Done", "outcome": "moved", "indexed": true, "doc_version_after": "..."}}
```
- `outcome`: `moved`, `unchanged` (already in that status), `refused_not_assignee`, `refused_by_source` or `source_unavailable`.
- When the caller may not open the issue, or it does not exist, the event has the usual denied decision (`doc_id_hash`, salted) and **no** `transition` block: the log records the attempt without naming the issue, as for a denied read.
- `from` is absent when Jira could not be read; `indexed` and `doc_version_after` are present only on `moved`.

**`conversation_title`**: `{"event_type": "conversation_title", "conversation_id": "c_1", "actor": {...}, "title": {"text": "DB migration blockers", "source": "generated" | "custom" | "cleared"}}`. A generated title's actor is the conversation's owner with `client: "brain"`, and `models.generator` names what wrote it: the model, `rules` when no model is configured or it failed, or `routine` for a routine's run (its title is the routine's name and the day). `/v1/conversations` takes the newest `custom` title that has not been `cleared`, else the newest `generated` one, else the first question. Only the question's text was given to the model, so the event carries no decisions and no `acl_label`.

**`routine_change`**: `{"event_type": "routine_change", "routine_id": "rt_...", "actor": {...}, "routine": {"action": "created" | "updated" | "paused" | "resumed" | "deleted", "question": "...", "schedule": {...}, "timezone": "..."}}`. `question`, `schedule` and `timezone` are present on `created` and `updated`. A pause the Brain makes itself (N3 and N4 in [docs/routines.md](../routines.md)) has `actor.user_id: "system:routines"` and a `reason`.

**`routine_skipped`**: `{"event_type": "routine_skipped", "routine_id": "rt_...", "actor": {"user_id": "system:routines", "client": "brain"}, "routine": {"owner": "<email>", "reason": "identity_unresolved" | "missed" | "error"}}`, so a routine's history has no silent gaps. Nothing was sent to a model.

**`ask` events from a routine run** have `actor.client: "routine"` (besides `ui` and `mcp`) and a top-level `routine_id`. `actor.user_id` is the owner, whose identity was resolved for that run. Everything else is as for a typed question, so replay and time-travel treat a run like any other ask.

**`trust` on `ask` events** *(proposed 13 Oct)*: `{"searched": ["jira", "slack"], "unavailable_sources": [], "per_source": {"jira": {"last_sync": "...", "status": "ok"}}, "stale_refetched": 0, "grounding": {"score": 0.96, "removed_claims": 0, "lower_confidence": 0} | null, "policy_version": "pol-0.3"}`: what the Trust panel showed the asker, kept so that a reopened answer can show it again ([api](api.md), conversations). None of it depends on a document the asker could not open: which sources the request searched, each source's sync state at that moment, and the checker's score for the claims that were shown. It holds no count of candidates or denied documents. Absent on events logged before 13 Oct.

Event types then read: `ask`, `search`, `mcp_call`, `audit_query`, `admin_view`, `acl_change_observed`, `alert_sent`, `checkpoint`, `identity_snapshot`, `issue_transition`, `conversation_title`, `routine_change`, `routine_skipped`.

## Events added for actions *(0.2, 14 Oct; not built yet)*
Five event types for [api](api.md) 0.6's `/v1/actions` ([ADR-010](../05-decisions/ADR-010-actions.md)), all in the hash chain. Each carries `plan_id`; together they answer "what did X create or post through Cortex, from which sources, and was any of it blocked?"

**`action_proposed`**: a plan was drafted, or changed by a `PATCH`.
```json
{"event_type": "action_proposed", "request_id": "req_...", "conversation_id": "c_1", "plan_id": "pl_7c1e",
 "actor": {"user_id": "priya@companya.com", "roles": ["engineer"], "client": "ui"},
 "query": {"text": "/breakdown @\"DB migration cutover plan\" in:DBMIG", "skill": "breakdown", "skill_version": 1, "skill_sha256": "<hash>"},
 "decisions": [{"doc_id": "confluence:ENG/db-cutover-plan", "allowed": true, "proof_path": ["..."], "...": "as on ask"}],
 "action": {"plan_sha256": "sha256:...", "payload_hash": "sha256:...", "operations": 6, "cited": ["confluence:ENG/db-cutover-plan"],
            "acl_label_sha256": "sha256:...", "revision": 0, "injection_flagged": false}}
```
- `decisions` are the read decisions for the sources (P1), as on `ask`: a source the caller may not open is a salted hash.
- `revision` is 0 for the first plan and counts each `PATCH`. `injection_flagged` is true when an operation was rejected for a destination the person did not name (F6).

**`action_decided`**: the policy result for each operation, written with every `action_proposed`.
```json
{"event_type": "action_decided", "plan_id": "pl_7c1e", "actor": {...},
 "action": {"policy_version": "pol-0.4", "operations": [
   {"index": 0, "type": "jira.create_issue", "destination": "jira:DBMIG", "allowed": true,
    "checks": {"P1": "pass", "P2": "pass", "P3": "pass", "P4": "pass", "P5": "pass", "P6": "pass"},
    "reason": null, "blocking_sources": [], "autofixes": [], "audience_snapshot_hash": "sha256:..."}]}}
```
- A destination the caller cannot see, or one that does not exist, is stored as `destination_hash` (salted), never in plain, as for denied documents. Audience members are never logged: only `audience_snapshot_hash`, which lets replay check P4 against the audience recorded then.

**`action_confirmed`**: `{"event_type": "action_confirmed", "plan_id": "...", "actor": {...}, "action": {"payload_hash": "sha256:...", "accepted": true | false, "reason": null | "plan_changed" | "plan_expired"}}`. A refused confirm is logged too.

**`action_executed`**: one per confirm that ran, `{"event_type": "action_executed", "plan_id": "...", "actor": {...}, "action": {"outcomes": [{"index": 0, "outcome": "created", "doc_id": "jira:DBMIG-160", "url": "...", "platform_status": 201, "indexed": true}]}}`. `outcome` has the values of the confirm response.

**`action_undone`** *(P1)*: `{"event_type": "action_undone", "plan_id": "...", "actor": {...}, "action": {"outcomes": [{"index": 0, "doc_id": "...", "outcome": "deleted" | "refused" | "failed"}]}}`.

None of them carries drafted text or field values, only hashes of them, so a blocked plan's text is not kept in the log; the conversation holds the plan for its owner. No write credential or token is ever written to an event.

Event types then read: `ask`, `search`, `mcp_call`, `audit_query`, `admin_view`, `acl_change_observed`, `alert_sent`, `checkpoint`, `identity_snapshot`, `issue_transition`, `conversation_title`, `routine_change`, `routine_skipped`, `action_proposed`, `action_decided`, `action_confirmed`, `action_executed`, `action_undone`.

## Changelog
- 0.2, 14 Oct (not built yet): `action_proposed`, `action_decided`, `action_confirmed`, `action_executed` and `action_undone`. ADR-010.
- 0.1, 13 Oct (proposed): `trust` on `ask` events.
- 0.1, 12 Oct: `issue_transition`, `conversation_title`, `routine_change` and `routine_skipped` events; `actor.client: "routine"` and `routine_id` on `ask` events.
- 0.1, 11 Oct: `checks.fields` (`pass`, `fail` or `nothing_to_check`: whether what the claims said about structured fields matched the documents') and `checks.field_mismatches` (`[{field, doc_ids}]` for each claim dropped, without the values) (fix F23).
- 0.1, 11 Oct: `query.projects` (ids of the registry projects the question named, among those the asker may see) (T7).
- 0.1, 11 Oct: `checks.grounding_lower_confidence` (claims kept between the grounding floor and threshold) and the `lower_confidence` value of `checks.grounding_model` (fix F21).
- 0.1, 11 Oct: `checks.leak_scan_docs`: how many documents the leak scan compared the answer with (fix F5). "clean" means clean against that many.
- 0.1, 11 Oct: `/verify` compares the log with a signed head kept outside the database (fix F4).
- 0.1, 11 Oct: `query.skill_version`, `query.skill_sha256` (the skill file pinned by hash) and `query.rewritten` (the standalone search text, set only when a follow-up was rewritten; `query.text` stays what the person typed).
- 0.1, 10 Oct: `identity_snapshot` events (time-travel queries).
- 0.1, 10 Oct: `links_followed` on `ask` events (documents added through stored links, each with the allowed document that linked to it). Denied link targets appear only as hashed entries in `decisions`.
- 0.1, 9 Oct: `conversation_id` on `ask` events; conversations are rebuilt from the log.
- 0.1: first draft.

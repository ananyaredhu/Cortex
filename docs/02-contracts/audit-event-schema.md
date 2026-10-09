# Audit event schema

version: 0.1 (draft, freezes Day 3)
Producer: Workstream B (audit service). Consumers: C (console, evals), B (audit agent).

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
  "query": {"text": "What's the status of the DB migration ...", "skill": "status-and-blockers"},
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
    "acl_label": ["user:priya@companya.com"]
  },
  "checks": {"deterministic": "pass", "grounding_model": "pass", "leak_scan": "clean"},
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

## Replayability
Each decision stores `acl_snapshot_hash`, `policy_version` and `doc_version` so an auditor can re-evaluate "was this decision correct at that time?". Together with ACL snapshots stored with validity intervals (bi-temporal), this supports time-travel queries ("what could jdoe see on 12 Oct?").

## `/verify`
`GET /v1/audit/verify` returns `{ok: true, checked: N, checkpoints: M}` or `{ok: false, first_broken_seq: K, reason: "..."}`. Must be fast enough to demo live.

## Querying
Audit queries (natural language or structured) are read-only and run **under RBAC**: only the compliance role can query other users' history. Querying the audit log is itself logged (`audit_query`). Sensitive lookups may require a two-person approval (stretch).

## Conversations
`ask` events carry `conversation_id` (the one returned by `/ask`). The Brain keeps no other record of conversations: `/v1/conversations` and `/v1/conversations/{id}` are rebuilt from these events, under the same ownership rule (only the asker's own events), so history survives a restart.

## Changelog
- 0.1, 9 Oct: `conversation_id` on `ask` events; conversations are rebuilt from the log.
- 0.1: first draft.

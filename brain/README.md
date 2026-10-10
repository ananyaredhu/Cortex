# Brain: policy plane, retrieval, pipeline, audit, API

Workstream B. The query path of [01-architecture](../docs/01-architecture.md) as code. Contracts:
[api](../docs/02-contracts/api.md) 0.2, [context-packet](../docs/02-contracts/context-packet.md),
[audit-event-schema](../docs/02-contracts/audit-event-schema.md), [acl-model](../docs/02-contracts/acl-model.md).

## Running it
```
make brain-api              # the real thing: Postgres index, simulators, real Slack and Drive from .env, bge-m3, :8000
make brain-api-fixture      # the real pipeline over the fixture corpus: no database, no model; /sim/advance applies e1 and e2
python -m brain.timing      # revocation-to-enforcement timing, B's stage on top of A's script
```
Auth is `Authorization: Bearer dev:<persona>` while `BRAIN_DEV_AUTH` is not `0`, or a JWT (HS256, `JWT_SIGNING_KEY`,
claims `sub`, `email`, `roles`, `aud`). The UI switches from the stub to this with `API_TARGET`.

bge-m3 loads at start-up (about 30 s warm, several minutes the first time). Nothing answers before it has.

## What a question goes through (`pipeline/graph.py`, one LangGraph node each)
| Node | Stage shown in the UI | What it does | Plane |
|---|---|---|---|
| identity | retrieve | `user:` token plus every connector's `resolve_identity().groups`; a failing or unmapped platform contributes nothing | policy |
| retrieve | retrieve | per-source fan-out, keyword leg (Postgres full-text, OR of the question's terms, ranked by distinct terms matched) and vector leg (bge-m3, `hnsw.iterative_scan`), both prefiltered on the asker's tokens, reciprocal-rank fusion | data |
| authorize | authorize | live `check_access` on every candidate, in parallel, short-TTL cache dropped by outbox events; any error is a deny; decisions carry `proof_path`, `acl_snapshot_hash`, `policy_version`, `doc_version` | policy |
| freshness | verify_live | `connector.version` against the indexed version; a changed document is re-fetched inline (`stale_refetched`) | policy |
| generate | generate | context packet (quotas, dedupe, snippet, sanitize and flag injection, attention order, budget), then the generator | LLM |
| check | check | layer 1: citations exist and were allowed, unsupported claims dropped, local denied-set scan (IDs, titles, canaries, 8-word shingles) | policy |
| finalize, audit | check | uniform refusal, api.md 0.2 response, hash-chained audit event; every request takes at least `BRAIN_FLOOR_LATENCY_MS` | |

Every request runs every node, refusals included, and `/ask/stream` always sends the same five stages.

## Generators (`gateway/`)
`GENERATOR_BACKEND` picks one (`auto` takes the first configured):
- `adp` (`gateway/adp.py`): Tencent Cloud ADP's Chat API with the published agent's `ADP_APP_KEY`. The context
  packet is the one message, our JSON-claims instruction goes in `SystemRole` (overriding the agent's prompt for the
  turn), online search is disabled for the turn, and the reply is read from the `response.completed` event.
  `GENERATOR_MODEL` is the label for the model the agent is configured with. The agent must be published and must
  have no knowledge base attached. Probe it with `python -m brain.gateway.probe`.
- `openai` (`gateway/models.py`): any `/chat/completions` endpoint with `GENERATOR_BASE_URL`, `GENERATOR_API_KEY`
  and `GENERATOR_MODEL`. Not yet run against a live endpoint.
- `template`: extractive, one claim per evidence item quoting its snippet. No model; the fallback.

Every backend abstains on any failure or unparseable reply: no unverified text reaches the checker.

## Audit (`audit/`)
`chain.py`: canonical JSON, SHA-256 links from a fixed genesis, Ed25519 checkpoints every 100 events, `verify`.
`store.py`: the one writer of `audit_events` (Postgres) or an in-memory log. `service.py`: queries under the
compliance role (logged as `audit_query`), answer text withheld unless the officer may see every cited document,
`/verify`, `/audit/replay`. Denied document IDs are stored salted-hashed (`AUDIT_DENIED_SALT`).

## Two runtimes (`runtime.py`)
`fixture_runtime`: fixture connectors, A's in-memory store filled by A's ingestion with the fake embedder, in-memory
index and audit log. `env_runtime`: Postgres, the simulators, real Slack and Drive, bge-m3, the configured generator.
The pipeline is the same object in both; tests run on the first, the demo on the second.

## Not yet (slice 2 and 3)
Router and query rewrite, an LLM generator in use, checker layers 2 and 3, the four skills, the MCP server,
link-edge expansion, conversation memory under ACL labels, time-travel queries, the audit agent.

`/v1/leakci/latest` serves the summary of the scoreboard C's Leak-CI wrote last (`python -m evals.leakci`,
`LEAKCI_SCOREBOARD`), and an empty one until a run exists.

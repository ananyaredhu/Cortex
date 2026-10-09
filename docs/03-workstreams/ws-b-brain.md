# Workstream B · Brain and policy

**Owner:** Ananya ([@ananyaredhu](https://github.com/ananyaredhu)) · **Directories:** `brain/`, `mcp_server/` · **Status file:** [status/ws-b.md](../status/ws-b.md)

## Mission
Turn authorized chunks into trustworthy, cited answers. Own the policy decision point, the query pipeline, the checker, the audit chain, the skills and the MCP server.

## Scope
- **Model gateway** with swappable backends and a **Day-2 model probe** ([ADR-001](../05-decisions/ADR-001-model-gateway.md)).
- **Policy decision point (PDP):** compute the asker's tokens, prefilter, call connector `check_access` just in time, record decisions with proof paths. Plain deterministic code, no LLM.
- **Retrieval:** hybrid (BM25 via Postgres FTS plus vectors), ACL prefilter, reciprocal-rank fusion, optional local rerank.
- **LangGraph pipeline:** route, rewrite, retrieve, JIT verify, link-edge expansion, context packet, generate, check, uniform refusal, audit.
- **Context engineering** and **context profile** ([context-packet](../02-contracts/context-packet.md)).
- **Runtime skills** (four) and the loader ([skills-format](../02-contracts/skills-format.md)).
- **Layered checker** ([ADR-003](../05-decisions/ADR-003-checker.md)): deterministic, local grounding model, small chat model.
- **Audit service:** hash chain, signed checkpoints, `/verify`, audit agent, replayable and time-travel queries ([audit-event-schema](../02-contracts/audit-event-schema.md)).
- **Answer-level ACL labels** and **stale-answer alerts**.
- **MCP server**, stateless ([ADR-004](../05-decisions/ADR-004-mcp.md), [mcp-tools](../02-contracts/mcp-tools.md)).
- Stretch: sensitivity-aware model routing, mosaic guard, honeytokens.

## Owns (produces)
[audit-event-schema](../02-contracts/audit-event-schema.md), [context-packet](../02-contracts/context-packet.md), [skills-format](../02-contracts/skills-format.md), [api](../02-contracts/api.md), [mcp-tools](../02-contracts/mcp-tools.md), and the PDP side of [acl-model](../02-contracts/acl-model.md).

## Consumes
Connectors, `chunks` schema and simulator admin endpoints from A. UI needs and golden tests from C.

## Tasks and schedule

### Days 1 to 2 (Fri 2 to Sat 3 Oct)
- [ ] **Model probe script:** reachability from Singapore, latency, rate limits, structured output, context length, cost for DeepSeek-V3 (ADP/TokenHub), Hunyuan, Qwen, small open-weight models (check #1). 9 Oct: `python -m brain.gateway.probe` exists; ADP probed (reachable, JSON claims, 3 to 14 s per call); rate limits, context length, cost and the other candidates still open
- [x] Model gateway interface with config-driven backends; embedding interface (bge-m3 default). 8 Oct: `brain/gateway/` (template generator in use; the OpenAI-compatible backend is wired but unrun, no key yet); the query vector uses A's bge-m3 embedder
- [x] Time bge-m3 and checker candidates on CPU (check #2, #6) with C. bge-m3 by A on 6 Oct; checker candidates 9 Oct (`python -m brain.checker.timing`): nli-deberta-v3-xsmall chosen, MiniCheck for offline grading, 3B chat model deferred to the server
- [x] LangGraph pipeline skeleton; audit hash-chain library with tests (genesis, append, verify, tamper test). 8 Oct: `brain/pipeline/graph.py`, `brain/audit/chain.py` with edit, delete, insert, reorder and checkpoint-signature tests
- [ ] Draft contract changes you need (PRs to `docs/02-contracts/`)

### Day 3 (Sun 4 Oct): contracts freeze
- [x] Ship a **stub** `/v1/ask`, `/v1/mywork`, `/v1/audit/*` returning fixtures that match [api](../02-contracts/api.md), so C can build the UI (shipped with the scaffold; C extended it to 0.2)
- [x] Agree the `chunks` schema with A (connector-interface 0.3; B's reads in `brain/retrieval/index.py`)

### Days 3 to 5 (Sun 4 to Tue 6 Oct)
- [x] PDP: token computation, prefilter, JIT `check_access` with short-TTL cache, fail closed, decisions with proof paths. 8 Oct: `brain/policy/`, caches dropped by the outbox consumer
- [x] Hybrid retrieval over the real index; per-source fan-out and fusion. 8 Oct: keyword leg ORs the question's terms (ranked by distinct terms matched), vector leg with the iterative scan and a measured 0.45 cutoff, RRF
- [x] Audit service: events, signed checkpoints, `/verify`. 8 Oct: `brain/audit/`, one writer of `audit_events`, Ed25519 checkpoints every 100 events, `/audit/query` under RBAC, `/audit/replay`

### Days 5 to 8 (Tue 6 to Fri 9 Oct)
- [ ] Router and query rewrite (small model); **context packet builder done** 8 Oct (`brain/packet.py`: quotas, dedupe, snippet, sanitize and flag, attention order, budget)
- [ ] Generator with structured claims plus citations; checker layers 1 and 2. 8 Oct: **layer 1 done** (`brain/checker/layer1.py`). 9 Oct: **generator done** through Tencent Cloud ADP (`brain/gateway/adp.py`, agent model GPT-5.6 Terra): all nine golden cases pass with it, zero canaries. **Layer 2 done** 9 Oct (`brain/checker/layer2.py`, nli-deberta-v3-xsmall, scores in the audit event)
- [x] Uniform refusal with timing normalization. 8 Oct: one refusal body, every node runs on every request, floor latency `BRAIN_FLOOR_LATENCY_MS`; tested equal for forbidden and nonexistent, 0.2 fields included
- [x] **Day 8 demoable:** scenarios 1, 3 and 4 run end to end on seed data. 8 Oct: all nine golden cases pass on the real pipeline in fixture mode (`evals/tests/test_golden_brain.py`); the non-scripted ones also on the Postgres index with the simulators and real Slack and Drive

### Days 9 to 10 (Sat 10 to Sun 11 Oct)
- [ ] Four skills and the loader (status-and-blockers, incident-investigation, design-discussion-summary, audit-inquiry)
- [ ] Stateless MCP server with per-request JWT validation; pin tool descriptions
- [ ] Link-edge authorization; answer-level ACL labels (8 Oct: labels computed and stored on every answer, `brain/policy/labels.py`; not yet used to gate memory); checker layer 3 for hard cases
- [x] Freshness read-through (version check and inline re-fetch). 8 Oct: `freshness` node; tested with e1 applied at the source but not ingested

### Days 11 to 12 (Mon 12 to Tue 13 Oct)
- [ ] Context profile; **`/v1/mywork` data done** 8 Oct (index rows through prefilter and live check)
- [x] Stale-answer alerts engine. 8 Oct: cited versions remembered per answer, compared with the index, served only for documents still visible
- [ ] Audit agent (NL to filters, under RBAC); **replay done** 8 Oct (`/v1/audit/replay`: revoked, edited, deleted per cited document); time-travel queries
- [x] Measure and publish revocation-to-enforcement time. 8 Oct, `python -m brain.timing --rounds 3` on the simulators: the next `/v1/ask` after the revocation refuses at p50 198 to 230 ms (p95 256 to 907 ms, first-connection outliers) across A's four scenarios, including the ingestion pass that writes the outbox event
- [ ] Stretch: sensitivity-aware model routing (restricted content only to in-region models)

### Days 13 to 15 (Wed 14 to Fri 16 Oct)
- [ ] Hardening, latency and cost tuning, final model choices recorded in ADRs
- [ ] Support the demo, video and submission

## Definition of done
- Scenarios 1 to 7 produce correct, cited answers and refusals.
- Zero leaks on the golden and red-team suites; the local denied-set scan has no hits.
- Every decision is in the audit log with evidence; `/verify` passes and fails correctly on tampering.
- The MCP server works from WorkBuddy and CodeBuddy with per-user identity.
- No credentials or denied content ever sent to a model or third party.

## Watch-outs
- The policy plane has no LLM influence on allow/deny.
- Never log or send denied content outside Zone 2; denied IDs are salted-hashed in the audit log.
- Hosted models see only authorized content; the denied-set scan stays local.
- Budget tokens: cache, small model where enough, run Leak-CI batches separately.

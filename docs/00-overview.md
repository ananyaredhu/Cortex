# 00 · Overview

## The problem
Company knowledge is scattered across wikis, tickets, chat and shared files, and staff lose about 30% of their week searching. An AI assistant that unifies **Confluence, Jira, Slack and Google Drive** could fix that, but "an assistant that can read everything can leak everything". So every answer has to be scoped to what the asker may see, and every retrieval and answer has to be recorded.

### What the system must do
- Integrate heterogeneous sources and **not flatten** each platform's permission model.
- **Permission-aware retrieval:** know the asker, filter *before* the LLM sees anything, and handle permissions changed after ingestion.
- Assemble context across platforms with a permission check on every piece.
- Keep a tamper-evident, complete, queryable audit trail (who, what, retrieved IDs, answer, time, allow or deny per document).
- Make the LLM safe: stop the model "filling in" content it was not given.
- Work through five worked scenarios: see [04-scenarios](04-scenarios.md).

## Thesis
> **The model proposes, a deterministic policy plane disposes.**
The LLM never sees unauthorized content, holds no credentials and has no write tools. Every claim in an answer traces to a chunk that was authorized at read time, and every decision is written to a hash-chained audit log.

## What is new here (calibrated)
Already common in 2026, so **not** claimed as new: query-time ACL filtering (Glean, Onyx Enterprise, Microsoft Copilot), hash-chained agent audit libraries, general discussion of MCP identity propagation.

What we add:
1. **Answer-level ACL labels.** Every derived artifact (answer, summary, cache entry, memory, vector) carries the intersection of its sources' ACLs.
2. **Closed, measured revocation window.** Re-verify at read time and publish revocation-to-enforcement time.
3. **Link-edge authorization.** Follow cross-platform links only if the target is also authorized.
4. **Post-generation leakage scan** against the denied set, run locally, with canary strings for testing.
5. **Replayable authorization audit.** Entries store the ACL snapshot hash and policy version; time-travel queries ("what could X see on date D?") with proof paths.
6. **Noninterference CI ("Leak-CI").** A user's answer must not change when documents they cannot see are added, removed or edited. Tested metamorphically.
7. **Stale-answer alerts.** If a source behind an answer changes, notify the people who received it.
8. **Identity-propagating, stateless MCP server.** CodeBuddy and WorkBuddy become real clients, with per-user identity and no shared master key.
9. **Red-team harness with a public scoreboard.**

We cannot prove nothing like this exists; we claim only that we found these gaps open in the products and papers we checked.

## What is in the system
- **Core:** connectors, policy decision point, retrieval, query path, five scenarios, audit chain, UI, deployment.
- **Differentiators:** Leak-CI, answer-level ACL labels, stale-answer alerts, replayable audit.
- **Further features:** runtime skills, context profile and context engineering, MCP server, My Work home, routines.

## Tencent products
- **ADP** (agent platform, DeepSeek models) is an optional generator backend; **Hunyuan** models and embeddings are optional backends too.
- **CodeBuddy and WorkBuddy** connect as MCP clients to the Brain's `/mcp` endpoint, with per-user identity: see [ADR-004](05-decisions/ADR-004-mcp.md) and [mcp-tools](02-contracts/mcp-tools.md).

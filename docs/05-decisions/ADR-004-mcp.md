# ADR-004 · Stateless MCP server

**Status:** Accepted.

## Context
MCP servers are a known leakage vector: shared service credentials, confused-deputy problems, and authorization checked only at session start. We want CodeBuddy and WorkBuddy to act as real clients with per-user identity.

## Decision
- **Stateless:** Python MCP SDK 2.x (`MCPServer`, renamed from FastMCP), streamable HTTP, `stateless_http=True`, mounted in the Brain process at `/mcp` when `BRAIN_MCP=1`.
- **Per-request identity:** every call carries a bearer JWT from our mock IdP, checked on every request by the SDK's bearer middleware through our verifier (`brain.auth.Authenticator`: signature, expiry, audience). The caller comes from the token, and no tool takes a user argument. No token passthrough to upstream systems; no shared master key.
- **Narrow, read-only tools** (`ask`, `search`, `get_source`, `explain_access`, `audit_query`). No raw "run a query" tool.
- Pinned tool descriptions and schemas, guarded by a fingerprint test. Every call audited as `mcp_call`, besides the Brain's own event.
- MCP is another front door into the same pipeline as the UI, with no separate logic.
- Conversation memory lives in our own store, not in MCP sessions.

## Consequences
- Per-request authorization, easy scaling, simple deployment.
- No server-initiated streaming or subscriptions. If later needed, add a stateful path separately.
- Clients: CodeBuddy supports `type: http` servers with `headers` and `${ENV}` variables; WorkBuddy documents STDIO, SSE and HTTP transports with custom headers.

## Built (Day 9)
`mcp_server/server.py`; 17 tests in `brain/tests/test_mcp.py`; `python -m mcp_server.smoke` runs 11 checks with the SDK's own client over real HTTP and passes.

## Findings
- The SDK's default `Host` check rejects any host but localhost with 421, so `BRAIN_MCP_ALLOWED_HOSTS` is a setting and a deployment step.
- The SDK advertises OAuth discovery metadata naming the Brain as authorization server. We do not implement OAuth, so clients must be given the token as a static header.
- Sync tools run on a worker thread with the request's context, so the blocking `ask` does not stall other requests.

## To verify
WorkBuddy remote MCP with an Authorization header and scheduling work in practice (check #7); which instruction file CodeBuddy reads (check #8).

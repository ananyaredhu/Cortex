# MCP tools

version: 0.2 (built in `mcp_server/server.py`; changes need all three reviewers)
Producer: Workstream B. Consumer: C (WorkBuddy and CodeBuddy setup).

The MCP server is **another front door into the same pipeline as the UI**. It contains no separate logic: each tool authenticates the caller, calls a Brain method (the one the HTTP API uses, or `search` / `get_source`) and returns the result. Permission checks, uniform refusals and the audit log are the ones the UI gets.

## Design
- **Opt-in:** served only when the Brain starts with `BRAIN_MCP=1`. Endpoint: `POST <BRAIN_PUBLIC_URL>/mcp`, JSON responses.
- **Stateless:** MCP Python SDK 2.x (`MCPServer`, renamed from FastMCP), streamable HTTP with `stateless_http=True`. No MCP session exists, so there is nothing to hold or hijack. Conversation memory lives in our own store, keyed by `conversation_id`.
- **Per-request identity:** every request carries `Authorization: Bearer <JWT>` (from the mock IdP, `POST /idp/token`). The SDK's bearer middleware calls our verifier on **every** request, which reuses `brain.auth.Authenticator` (signature, expiry, audience; dev tokens only when `BRAIN_DEV_AUTH=1`). The caller is the person in the token and **no tool takes a user argument**. There is no shared service credential and no token is passed to any other system.
- **Narrow, read-only tools.** Five tools. No raw "run a query" tool.
- **Pinned tools:** the names, descriptions and input schemas are fixed. `brain/tests/test_mcp.py` holds a SHA-256 fingerprint of them and fails if they change. Changing a description is a contract change.
- **Same refusals as the UI:** a document the caller cannot open and one that does not exist look identical in every tool (`found: false`, an empty `results`, or the standard refusal).
- **Audited:** every call writes an `mcp_call` event (`detail: {tool, ok}`, `actor.client: "mcp"`), **besides** the Brain's own event for `ask`, `search` and `get_source` (also `actor.client: "mcp"`). A refused call is logged with `ok: false`.
- **Host check:** the SDK rejects any `Host` header not in `BRAIN_MCP_ALLOWED_HOSTS` (DNS-rebinding guard; default `localhost:*,127.0.0.1:*`). Add the real hostname when deploying.
- **Production:** `BRAIN_ENV=production` refuses to start with MCP on and a `BRAIN_PUBLIC_URL` that is not `https`.

## Tools
| Tool | Arguments | Result |
|---|---|---|
| `ask` | `question`, optional `conversation_id`, optional `skill_hint` | The same object as `POST /v1/ask` (`answer`, `claims`, `citations`, `refused`, `abstained`, `generator_unavailable`, `freshness`, `coverage`, ...) |
| `search` | `query`, optional `sources` (list of `confluence`, `jira`, `slack`, `gdrive`), optional `limit` (default 5, at most 10) | `{"results": [{doc_id, title, url, source, as_of, snippet, why_visible, flags}]}`. Authorized hits only; `[]` means nothing the caller can open matches |
| `get_source` | `doc_id` | `{"found": false}`, or `{"found": true, doc_id, title, url, source, as_of, text, truncated, why_visible, flags}`. Text is read live from the source, cleaned of instruction-like sentences, and cut at 8,000 characters |
| `explain_access` | `doc_id` | `{"found": false}` or `{"found": true, "proof_path": [...]}` |
| `audit_query` | optional `question`, optional `filter` (`user`, `space`, `from`, `to`, `decision`) | Compliance role only. With `filter`: `{"events": [...], "count": n}` as `POST /v1/audit/query`. With only `question` (0.2, 10 Oct): the audit agent's answer as `POST /v1/audit/ask` (`plan`, `summary`, `result`, or `clarify`). Anyone else gets a tool error: "the compliance role is required". Chain verification stays at `GET /v1/audit/verify` |

## Client configuration
Get a token (demo IdP; it lives `BRAIN_MOCK_IDP_TTL_S` seconds, default 900, so raise it for a long demo):
```
curl -s -X POST https://<host>/idp/token -H 'Content-Type: application/json' -d '{"persona": "jordan"}'
```
CodeBuddy (`~/.codebuddy/.mcp.json` or project `.mcp.json`):
```json
{
  "mcpServers": {
    "internal-brain": {
      "type": "http",
      "url": "https://<host>/mcp",
      "headers": { "Authorization": "Bearer ${BRAIN_TOKEN}" }
    }
  }
}
```
Keep the token in an environment variable. Never commit it or show it in screenshots. WorkBuddy documents HTTP servers with custom headers too; the first real connection from WorkBuddy is still to be confirmed (see [06-risks-and-checks](../06-risks-and-checks.md), check #7).

**Static header only.** The SDK also publishes standard OAuth metadata at `/.well-known/oauth-protected-resource/mcp`, naming the Brain as the authorization server. The Brain does not implement the OAuth authorize/token endpoints (the mock IdP is a different, simpler call), so a client must be given the token as a header; one that insists on OAuth discovery will not connect.

## Check a deployment
`python -m mcp_server.smoke --url https://<host>` signs in as Priya, Sam and Jordan through the mock IdP and runs 11 checks with the SDK's own client (see `mcp_server/README.md`).

## Use in the demo
- Jordan (compliance) asks the audit question from **WorkBuddy**; a **scheduled WorkBuddy task** produces a weekly compliance digest from `audit_query`.
- Developers query project knowledge from **CodeBuddy** with their own identity.
- A client's own model sits on top and may rephrase what a tool returns. The tools return `claims` and `citations` for that reason: a client should show them as they are.

## Changelog
- 0.2: built. SDK 2.x `MCPServer`; endpoint, settings and results written down; no user argument; host check; pinned fingerprint; `audit_query` no longer returns verify status.
- 0.1: first draft.

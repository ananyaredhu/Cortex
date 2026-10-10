# MCP server

Lets WorkBuddy, CodeBuddy or any MCP client use the Brain with the signed-in person's own permissions. It is another front door into the same Brain as the UI: same permission checks, same refusals, same audit log. Contract: [docs/02-contracts/mcp-tools.md](../docs/02-contracts/mcp-tools.md). Decision: [ADR-004](../docs/05-decisions/ADR-004-mcp.md).

| File | What |
|---|---|
| `server.py` | The five tools, the token verifier, the `mcp_call` audit wrapper, and `build_mcp_app` (mounted by `brain/api/app.py` when `BRAIN_MCP=1`) |
| `smoke.py` | A pass/fail check of a running server, using the MCP SDK's own client |

## Try it in two minutes (no database, no accounts)
```
# window 1: the Brain, fixture mode, mock IdP and MCP on, dev login off
BRAIN_RUNTIME=fixture BRAIN_MOCK_IDP=1 BRAIN_DEV_AUTH=0 BRAIN_MCP=1 JWT_SIGNING_KEY=<64 hex characters> \
  .venv/bin/uvicorn brain.api.main:app --port 8000

# window 2: sign in as three people and run 11 checks
.venv/bin/python -m mcp_server.smoke --url http://localhost:8000
```
A 64-character key: `.venv/bin/python -c "import secrets; print(secrets.token_hex(32))"`. On Windows use `.venv\Scripts\python` and set the variables with `$env:NAME="value"` first.

## Connect a client
1. Get a token: `curl -s -X POST http://localhost:8000/idp/token -H "Content-Type: application/json" -d '{"persona":"jordan"}'`. It lasts `BRAIN_MOCK_IDP_TTL_S` seconds (900 by default; raise it for a long demo).
2. Put the `access_token` in an environment variable (`BRAIN_TOKEN`), never in a file you commit.
   CodeBuddy's `codebuddy mcp add-json` expands `${BRAIN_TOKEN}` while saving and writes the real token into `.mcp.json`. Add the server without the variable set, or edit the file afterwards so the header reads `Bearer ${BRAIN_TOKEN}` again (checked: `grep -c eyJ ~/.codebuddy/.mcp.json` must print 0).
3. Point the client at `http://localhost:8000/mcp` with the header `Authorization: Bearer ${BRAIN_TOKEN}`. CodeBuddy's `.mcp.json` example is in the contract.

## Settings
| Variable | Meaning |
|---|---|
| `BRAIN_MCP=1` | Serve `/mcp` (off by default) |
| `BRAIN_PUBLIC_URL` | The Brain's address as clients see it. Must be `https` in production |
| `BRAIN_MCP_ALLOWED_HOSTS` | `Host` headers accepted, comma separated. Add the real hostname when deployed, or every request gets 421 |
| `JWT_SIGNING_KEY`, `JWT_AUDIENCE` | Tokens are checked with these on every request |

## Rules for changing it
- A tool never takes a user or email argument: the caller is the person in the token.
- A tool never contains permission logic: call a Brain method.
- Changing a tool's name, description or input schema changes what a client's model reads: update the contract, then the fingerprint in `brain/tests/test_mcp.py`, with all three reviewers.

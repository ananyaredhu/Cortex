# Contracts

These files are the **agreements between components**. Code in one component depends on these shapes, not on another component's internals.

| Contract | Defines | Producer | Consumers |
|---|---|---|---|
| [connector-interface](connector-interface.md) | `Connector` protocol, `Document`, `Chunk`, `AccessDecision` | Connectors | Brain |
| [acl-model](acl-model.md) | Principals, ACL tokens, per-source semantics, labels on derived artifacts | Connectors (evaluators), Brain (PDP) | Connectors, Brain, UI |
| [audit-event-schema](audit-event-schema.md) | Audit event fields, hash chain, `/verify` | Brain | UI, evals |
| [context-packet](context-packet.md) | What the LLM plane receives | Brain | Brain |
| [skills-format](skills-format.md) | Runtime skill files | Brain | Brain, UI |
| [api](api.md) | HTTP API used by the UI | Brain | UI |
| [mcp-tools](mcp-tools.md) | MCP tool definitions | Brain | MCP clients (CodeBuddy, WorkBuddy) |

## Rules
- A contract changes in the same pull request as the code that depends on it, reviewed by whoever owns the producing and the consuming side.
- Version each contract with a `version:` line at the top. Bump it on any change and note it in the changelog at the bottom of the file.
- Each component has a **stub** of its side (`connectors/stub/`, `brain/stub_api/`) returning fixture data that matches the contract, so the others can be built and tested against it.
- Examples in these files are fixtures: copy them into tests.
- Field names are `snake_case`. Timestamps are UTC ISO-8601. IDs are opaque strings.

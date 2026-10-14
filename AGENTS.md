# Engineering conventions

For anyone working in this repo, human or agent.

## Ground rules
1. **Docs are the source of truth.** Read `docs/01-architecture.md` and the relevant `docs/02-contracts/*.md` before changing code. If code and a contract disagree, fix the contract first.
2. **The policy plane has no LLM.** Anything that decides who may see what is plain, deterministic, tested code in `brain/policy/`.
3. **The LLM plane is untrusted.** It gets only sanitized, already-authorized context. No credentials, no network except to the policy plane, no write tools. It may return a *proposed* action plan as data; the policy plane decides it and a person confirms it.
4. **Never commit secrets.** Keys live in `.env` (gitignored). `.env.example` has names only. Never paste keys, tokens or redemption codes into prompts, issues or screenshots.
5. **Denied content never leaves the trust boundary.** Do not send documents the asker may not see to any model or third-party service.
6. **Every derived artifact carries ACL labels** (answers, summaries, caches, memory, vectors). See `docs/02-contracts/acl-model.md`.
7. **Uniform refusals.** A denied document and a nonexistent document must produce the same response shape and similar timing.
8. **No write widens an audience.** Every write passes the audience rule in `docs/02-contracts/acl-model.md` ("Writes"): everyone who can read the destination could already read every source.

## Layout
`connectors/` and `simulators/` are the sources and ingestion. `brain/` and `mcp_server/` are the policy plane, pipeline and MCP front door. `ui/`, `evals/` and `deploy/` are the web app, the security evaluations and the deployment.

## Code
- Python 3.12 for the backend, type hints required, `ruff` and `pytest`.
- Every connector implements `docs/02-contracts/connector-interface.md` and must pass the shared contract tests against both real and simulated backends.
- New behavior needs a test; security behavior needs a test in `evals/`.
- Small pull requests, one concern each. A contract changes in the same pull request as the code that depends on it.

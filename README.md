# Cortex

**A permission-aware AI knowledge system over Confluence, Jira, Slack and Google Drive.** Ask a question in plain language and get a cited answer built only from what *you* are allowed to open, with a tamper-evident record of every question, every document considered and every decision.

Built for the FinTech track (Aspire) of the Tencent Cloud AI CAN DO IT Hackathon Singapore 2026.

> **The model proposes, a deterministic policy plane disposes.** The LLM never sees content the asker may not see, holds no credentials, has no write tools, and every decision lands in a hash-chained audit log.

## The problem
Company knowledge sits in four kinds of tool, each with its own rules about who may see what, and staff lose about a third of their week searching it. An assistant that can read everything can also tell everyone everything. Cortex unifies the four sources and keeps each platform's permission model intact, end to end.

- A backend engineer asks *"What's the status of the database migration, and were there blockers raised in Slack last week?"* and gets one answer, cited to the Jira ticket and the Slack thread, and nothing from a private channel she is not in.
- A contractor asks for the Q3 security breach report and gets exactly the same reply as for a report that does not exist: no hint that it is there.
- A compliance officer asks *"What did Priya access in the PAY space in the last 30 days?"*, or *"What could Priya see on 5 October?"*, and gets a reconstruction from a log that proves it has not been altered.

## How it works
```
question ──► identity ──► understand ──► retrieve ──► authorize ──► expand ──► freshness ──► generate ──► check ──► finalize ──► audit
             (who asks)   (skill,        (ACL tokens  (live check   (linked    (is the       (LLM sees    (every    (uniform     (hash-chained,
                          follow-ups,     prefilter,   per document, documents,  index         only the     claim is  refusals,    signed head)
                          project)        keyword +    never cached  one hop,    current?)     packet)      grounded) citations)
                                          vector)      past a TTL)   re-checked)
```
**Policy plane (no LLM).** Plain, deterministic, tested code in `brain/policy/` decides who may see what. Retrieval is prefiltered on ACL tokens, every candidate is checked again live against the source's own permission model, and a denied document and a nonexistent one produce the same response shape with similar timing.

**LLM plane (untrusted).** The model receives a sanitized *context packet* of already-authorized text. It has no credentials, no network except to the policy plane and the model gateway, and no write tools. Its answer is checked claim by claim against the packet (a layered checker) and scanned for any text from documents the asker may not see.

**Audit.** Every request, retrieval, decision and answer is an append-only event in a hash chain with Ed25519 checkpoints. A signed head is also written outside the database, so deleting the newest events is detected. Each event stores the ACL snapshot hash and policy version, so a decision can be replayed.

## What is distinctive
1. **Answer-level ACL labels:** every derived artifact (answer, summary, cache entry, memory, vector) carries the intersection of its sources' ACLs.
2. **A closed, measured revocation window:** permissions are re-verified at read time, and the revocation-to-enforcement time is measured.
3. **Link-edge authorization:** links between platforms are followed only if the target is authorized too.
4. **Post-generation leakage scan** against the denied set, run locally, with canary strings for testing.
5. **Replayable authorization audit:** "what could X see on date D?" time-travel queries with proof paths, and an audit agent that turns a compliance officer's question into a validated query plan.
6. **Noninterference CI (Leak-CI):** a person's answer must not change when documents they cannot see are added, removed or edited.
7. **Stale-answer alerts:** if a source behind an answer changes, the people who received it are told.
8. **A stateless MCP server** with per-user identity, so CodeBuddy and WorkBuddy become real clients with no shared master key.
9. **A red-team harness** that attacks through the API (injection in a document and in a question, existence probes, a revocation race) and publishes a scoreboard.

Also built: runtime skills (hash-pinned task procedures), follow-up questions and conversation memory (re-checked against access), a project registry that boosts but never filters, routines (a saved question asked again on a schedule, as its owner), and a *My Work* home.

## What is in the repo
```
brain/         policy decision point, retrieval, pipeline, checker, audit, skills, model gateway, HTTP API
connectors/    real Slack and Google Drive connectors, ingestion service, shared connector base
simulators/    Confluence and Jira simulators (free plans have no permission model), with admin controls
mcp_server/    stateless MCP server: the same pipeline for CodeBuddy, WorkBuddy and other MCP clients
ui/            the Cortex web app: Ask, My Work, Team focus, Routines, Compare people, Audit console, Admin
evals/         golden cases, Leak-CI, red-team harness, security tests
fixtures/      "Company A": five personas, 27 documents across the four sources, scripted events
deploy/        Docker Compose, Caddy (HTTPS) and the deployment guide
db/            Postgres + pgvector schema
docs/          architecture, contracts, scenarios, decision records
```
Technology: Python 3.12 (FastAPI, LangGraph), Postgres with pgvector, local bge-m3 embeddings, React + TypeScript + Vite for the UI, MCP over streamable HTTP, Tencent Cloud ADP as an optional generator backend.

## Run it
You need Python 3.10 or newer (3.12 recommended), Node 22 and Docker (only for the real database).

```bash
make setup     # virtualenv, dependencies, secret-scan hook
make test      # contract tests, golden cases and security tests
```

**See the product without a database or a model download** (the stub API serves the fixture corpus; sign in as any persona):
```bash
make stub-api            # the API on :8000
make ui                  # the UI on http://localhost:5173
```

**Run the real pipeline over the fixtures** (policy plane, retrieval, checker, audit chain; no database):
```bash
make brain-api-fixture   # the Brain on :8000, with scripted events for the demo scenarios
```

**Run the whole thing for real** (Postgres, simulators, Slack and Drive, ingestion, the Brain and the UI behind HTTPS): follow [deploy/README.md](deploy/README.md), and [docs/end-to-end-testing.md](docs/end-to-end-testing.md) to check it. Copy `.env.example` to `.env` and fill in your own keys; `.env` is gitignored.

The UI has its own guide in [ui/README.md](ui/README.md), the Brain in [brain/README.md](brain/README.md) and the MCP server in [mcp_server/README.md](mcp_server/README.md).

## Tests and security evaluation
| What | Command |
|---|---|
| Unit, contract and golden tests (every connector must pass the same contract tests, real and simulated) | `make test` |
| Lint | `make lint` |
| UI unit tests, type check and build | `cd ui && npm test && npm run build` |
| UI end-to-end and accessibility tests (Playwright) | `cd ui && npm run e2e` |
| Leak-CI: planted, edited and removed hidden documents must not change anyone else's answer | `make leakci` |
| Red team: injection, existence probes, revocation race | `python -m evals.redteam --target fixture` |
| Golden cases against a running Brain | `python -m evals.golden --target http://localhost:8000` |

## Documentation
| Doc | What it is |
|---|---|
| [00-overview](docs/00-overview.md) | Problem, thesis and what is new |
| [01-architecture](docs/01-architecture.md) | Zones, trust boundaries and the query path |
| [02-contracts/](docs/02-contracts/) | The interfaces between components: connectors, ACL model, API, audit events, context packet, skills, MCP tools |
| [04-scenarios](docs/04-scenarios.md) | The five scenarios and the two CTO questions, each with a golden test |
| [05-decisions/](docs/05-decisions/) | Decision records |
| [company-a-seed](docs/company-a-seed.md) | The fictional company every source is seeded with |
| [routines](docs/routines.md) | Scheduled questions: design, security properties and tests |
| [end-to-end-testing](docs/end-to-end-testing.md) | Full test on the real stack |

Engineering rules that the code relies on are in [AGENTS.md](AGENTS.md).

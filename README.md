# Internal Brain

A permission-aware AI knowledge system over Confluence, Jira, Slack and Google Drive, built for the **FinTech track (Aspire)** of the Tencent Cloud AI CAN DO IT Hackathon Singapore 2026.

> **Thesis:** the model proposes, a deterministic policy plane disposes. The LLM never sees content the asker may not see, holds no credentials, has no write tools, and every decision lands in a hash-chained audit log.

**Deadline: Fri 16 Oct 2026** (target freeze Thu 15 Oct). Day 1 = Fri 2 Oct. Demoable build due **Day 8 = Fri 9 Oct**.

## Start here (10 minutes)
1. Read [docs/00-overview.md](docs/00-overview.md), then [docs/01-architecture.md](docs/01-architecture.md).
2. Open your workstream file and pick the first unchecked task:
   | Workstream | Owner | File |
   |---|---|---|
   | **A** Sources and freshness | Praew ([@watersalamander](https://github.com/watersalamander)) | [ws-a-sources.md](docs/03-workstreams/ws-a-sources.md) |
   | **B** Brain and policy | Ananya ([@ananyaredhu](https://github.com/ananyaredhu)) | [ws-b-brain.md](docs/03-workstreams/ws-b-brain.md) |
   | **C** Experience and proof | Guanyue ([@guanyue017-dev](https://github.com/guanyue017-dev)) | [ws-c-experience.md](docs/03-workstreams/ws-c-experience.md) |
3. Read the contracts you own or consume in [docs/02-contracts/](docs/02-contracts/). **Contracts freeze on Day 3 (Sun 4 Oct).**
4. Run `make setup` then `make test` (see "Work in parallel" below; Windows commands are there too). Copy `.env.example` to `.env` and fill in your own keys. Never commit `.env`.

## Work in parallel: shared fixtures, stubs and tests
Nobody has to wait for anyone else's code. Everything below works from a fresh clone.

| Need | Use | Command |
|---|---|---|
| Data to build against | [fixtures/](fixtures/): 5 personas, 18 documents across the four sources, scripted events, 9 golden cases | `make fixtures` regenerates `company_a.json` |
| A connector to call (B) | `connectors/stub/fixture_connector.py` implements the [connector interface](docs/02-contracts/connector-interface.md) | |
| A Brain API to call (C) | `brain/stub_api/`: stub of the [HTTP API](docs/02-contracts/api.md); auth header `Authorization: Bearer dev:priya` | `make stub-api` |
| A database (A, B) | Postgres + pgvector with the draft schema in `db/init.sql` | `make db-up` |
| Tests that keep us honest | `connectors/tests/contract/` (every connector must pass), `evals/` (golden cases, security properties) | `make test` |
| Leak-CI (C) | `evals/leakci.py`: plants, edits and removes documents an asker may not see and checks the answer does not change; writes the scoreboard the Admin page shows | `make leakci` |

First time: `make setup` (creates `.venv`, installs dependencies, enables the secret-scan hook), then `make test`. CI runs lint and the same tests on every pull request. Needs Python 3.10 or newer (3.12 recommended).

No `make` (for example on Windows)? Run the same steps by hand from the project root:
```
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\pre-commit install
.venv\Scripts\python -m pytest
```
(On macOS or Linux, replace `.venv\Scripts\` with `.venv/bin/`.)

**PyCharm:** run these in the built-in Terminal at the project root. Then set the interpreter to the new `.venv` (Settings, Project, Python Interpreter, Add Interpreter, Existing, `.venv/bin/python`) and set Tools, Python Integrated Tools, Default test runner to pytest.
When your real piece is ready, register it in the contract tests and point the golden runner at it; the stubs stay as the reference.

## Doc map
| Doc | What it is |
|---|---|
| [00-overview](docs/00-overview.md) | Problem, thesis, innovations, judging map |
| [01-architecture](docs/01-architecture.md) | Zones, trust boundaries, query path |
| [02-contracts/](docs/02-contracts/) | Shared interfaces between workstreams |
| [03-workstreams/](docs/03-workstreams/) | Scope, tasks and day-by-day plan per person |
| [04-scenarios](docs/04-scenarios.md) | The 5 handbook scenarios + 2 CTO questions, with golden tests |
| [05-decisions/](docs/05-decisions/) | Short decision records (ADRs) |
| [06-risks-and-checks](docs/06-risks-and-checks.md) | Things to verify in week 1 |
| [07-submission](docs/07-submission.md) | Handbook requirements, proof log, checklist |
| [plain-english](docs/plain-english.md) | Non-technical plan book |
| [status/](docs/status/) | One status file per workstream |

## Repo layout
```
connectors/    real Slack + Drive connectors, shared connector base
simulators/    Confluence + Jira simulators (and optional Slack/Drive sims for scale)
brain/         policy decision point, retrieval, pipeline, checker, audit, skills, model gateway
mcp_server/    stateless MCP server (front door for CodeBuddy/WorkBuddy)
ui/            chat, My Work, admin and audit console
evals/         Leak-CI, red-team harness, golden tests
deploy/        Singapore deployment
docs/          everything above
```

## Working agreements
- **Trunk-based.** Short-lived branches named `ws-a/<topic>`, `ws-b/<topic>`, `ws-c/<topic>`; PRs into `main`.
- **Reviews.** One review for code. **All three** for anything in `docs/02-contracts/` or `docs/05-decisions/`.
- **Stubs by Day 3.** Each workstream ships a stub of its interface so nobody blocks.
- **Status.** Update your own file in `docs/status/` at the end of each day (separate files, no merge conflicts).
- **Daily sync**, 15 minutes. Day 8 is a demo and schedule review.
- **Secrets.** `.env` only. A pre-commit secret scan runs (`pre-commit install`). Keys and redemption codes never appear in screenshots.
- **Proof of CodeBuddy/WorkBuddy use** is mandatory for scoring. Log every screenshot in [docs/07-submission.md](docs/07-submission.md).
- **Build-time models.** In WorkBuddy, use Hy3 / Hy4 preview (shown as 0.00x) for routine work, Fast/Balanced for medium tasks, and keep the ~3x models for hard design and review. Confirm what the multipliers mean in your credits dashboard.

## Status
Planning complete, build starting. See [docs/status/](docs/status/).

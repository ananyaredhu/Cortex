# Workstream A · Sources and freshness

**Owner:** Praew ([@watersalamander](https://github.com/watersalamander)) · **Directories:** `connectors/`, `simulators/` · **Status file:** [status/ws-a.md](../status/ws-a.md)

## Mission
Get real data, with faithful permissions, from the four platforms into the index quickly and correctly. Own freshness end to end.

## Scope
- **Real connectors:** Slack (free workspace, custom internal app, Events API) and Google Drive (several free Google accounts, per-user OAuth, `changes.watch`).
- **Simulators:** Confluence and Jira with faithful permission semantics (a real free Atlassian tenant optionally for content and webhooks only). Optional Slack/Drive simulators for scale and attack tests.
- **ACL evaluators** per source, identity mapping, `check_access`.
- **Ingestion service:** consumes `list_changes` (webhooks plus poll fallback), chunks, embeds (local bge-m3), writes chunks with ACL tokens to Postgres.
- **Seed data** for fictional "Company A" and a large-scale generator (12k+ Confluence pages, 200+ Slack channels).
- **Contract tests** run against both real and simulated backends.

## Owns (produces)
[connector-interface](../02-contracts/connector-interface.md) and the ACL evaluators in [acl-model](../02-contracts/acl-model.md); the `chunks` table schema; simulator admin endpoints (revoke, restrict, edit, add/remove hidden docs).

## Consumes
- Audit-event schema (emit `acl_change_observed`): from B.
- Embedding backend choice: [ADR-002](../05-decisions/ADR-002-embeddings.md).

## Tasks and schedule
Day 1 = Fri 2 Oct. Day 3 = contracts freeze. Day 8 = demoable build.

### Days 1 to 2 (Fri 2 to Sat 3 Oct)
- [x] Create the Slack workspace and a custom internal app; verify rate limits for an internal app (check #3 in [06-risks](../06-risks-and-checks.md)): exempt from the 1-request-per-minute cut; not capped at 15 objects either (confirmed 6 Oct, PR #22)
- [x] Create 4 to 5 Google accounts for personas; set up OAuth client; verify `changes.watch` reaches a webhook (check #4) (accounts and OAuth: Priya, Dana and Maya signed in, 5 Oct, which covers the owner or an editor of every seeded file; `changes.watch` reached the webhook through a tunnel and check #4 closed, 6 Oct, PR #19)
- [x] Decide the Atlassian approach: trial window, sandbox, or simulator only (check #5). Decided: simulator only
- [x] Design the "Company A" seed corpus and personas (Priya, Sam, Dana, Jordan, manager); write it down in `docs/` for the team: [company-a-seed.md](../company-a-seed.md)
- [x] Draft contract changes you need (PRs to `docs/02-contracts/`): contracts 0.2, PR #1

### Day 3 (Sun 4 Oct): contracts freeze
- [x] Ship connector and ingestion **stubs** that return fixture data matching the contracts (fixture connector; the real ingestion service took the place of a stub)
- [x] Postgres schema for `chunks` (pgvector, FTS, GIN on `acl_tokens`) agreed with B: connector-interface 0.3, "Reading the index" (6 Oct; written by A because B has not started; B reviews the PR)

### Days 3 to 5 (Sun 4 to Tue 6 Oct)
- [ ] Slack connector: public/private channels, membership, threads, DMs; Events API plus poll fallback; `check_access` via channel membership (merged and run on real Slack, PRs #9, #10, #12; Events API through Socket Mode added 6 Oct, PR #20, with polling as the fallback, and run on real Slack; left unticked because DMs are not built: they need wider scopes)
- [ ] Drive connector: folders, files, sharing roles, inheritance, shared drives, external sharing; `changes.watch`; `check_access` via permissions API (merged and run on real Drive, PRs #11 and #13; seed manifest and `changes.watch` push notifications added 6 Oct, PR #19; left unticked because shared drives are not built)
- [x] Confluence simulator: spaces, nested pages, space permissions, inherited page restrictions, webhooks, REST shapes similar to the real API
- [x] Jira simulator: projects, role schemes, groups, **issue security levels**, issues with links, webhooks

### Days 5 to 7 (Tue 6 to Thu 8 Oct)
- [x] Identity mapping across platforms (`resolve_identity`); fail closed when unmapped (loader PR #7; the Slack and Drive connectors use it and give unmapped accounts no access, PRs #9 and #11)
- [x] Ingestion service: chunking, local bge-m3 embedding (store model and version), ACL tokens, deletes, `acl_change` handling (PRs #5, #6 and #14; bge-m3 run on CPU 6 Oct, check #6)
- [x] Seed Company A data in all four sources, including the scenario fixtures (breach report in a security-only space, private channel, runbook, DB-migration project, auth-service thread): all 18 documents indexed and checked with `check_index`, 6 Oct

### Day 8 (Fri 9 Oct): demoable build
- [x] Data flows from all four sources into the index with correct ACL tokens (6 Oct: 18 documents, bge-m3; checked with `python -m connectors.ingestion.check_index`)
- [x] Simulator admin actions work (revoke, restrict, edit)

### Days 9 to 10 (Sat 10 to Sun 11 Oct)
- [x] Contract tests pass on real and simulated backends (inheritance, revocation, edit, negative case) (6 Oct, PR #21: real Slack and real Drive registered, opt-in with `CONTRACT_REAL`; all pass, except restriction and container revocation, which are expected failures because the scopes are read-only, and Slack's revocation needs a hand step; simulators and fakes pass all)
- [x] Scale seed generator: 12k+ pages, 200+ channels; measure ingestion throughput (7 Oct, PR #23: 12,000 pages and 220 channels on the simulators, a Slack simulator added; about 12 documents/s into Postgres, bounded by per-document writes; the index checked at that scale)
- [x] Measure and expose freshness lag (`freshness_lag_seconds`, p50/p95) (6 Oct, PR #22: samples in `ingestion_lag`, last run per source in `ingestion_sources`, report for `GET /v1/freshness` proposed; measured on all four sources, see the status file)

### Days 11 to 12 (Mon 12 to Tue 13 Oct)
- [x] Hidden-document add/remove/edit tooling for Leak-CI (with C) (7 Oct, `python -m simulators.leakci`: plant, edit, remove, list, clear and verify, on Confluence and Jira; C wires it into the Leak-CI tests)
- [x] Webhook and poll hardening, rate-limit back-off, retries (7 Oct: a failing source no longer stops the others or the process; per-source back-off with `Retry-After`; database reconnect; expired simulator cursors recrawl on their own, contract 0.3 addition)
- [x] Support B on revocation-to-enforcement timing measurement (7 Oct: `python -m connectors.ingestion.revocation_timing` times A's stages on four simulator scenarios, with a hook for B's `/v1/ask` stage; live `check_access` denies within about 5 to 8 ms)

### Days 13 to 15 (Wed 14 to Fri 16 Oct)
- [ ] Bug fixes, final data reset, deployment support, `simulators/README.md` (tools ready 7 Oct: `python -m connectors.reset_demo` checks and resets the demo data, `connectors/DEPLOY.md` is for C's deployment, `simulators/README.md` is current; the final reset itself runs just before submission)

## Definition of done
- All four connectors pass the shared contract tests.
- A revocation in any source flips `check_access` to deny and emits `acl_change` within the SLA.
- A content edit appears in the index within the SLA (target: under 5 minutes for events, under 1 hour worst case).
- No credentials in the repo; read-only scopes only.

## Watch-outs
- Do not flatten permission semantics. Keep native evidence in `AclEvidence.native`.
- `check_access` fails closed on errors and timeouts.
- Slack free-plan history and rate limits: confirm early. Atlassian free plan lacks the permission features we need.

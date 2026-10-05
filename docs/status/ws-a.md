# Status · Workstream A (Sources and freshness)

Update at the end of each day. Newest first. Keep it short: done, next, blockers.

## Mon 5 Oct (Day 4)
- **Done:** Slack connector merged (PR #9) and verified on the real "Company A" workspace: all 5 channels and 6 threads found, tokens and text match the fixtures, access matches for Priya, Dana and Maya. Check #3: exempt from the 1-request-per-minute cut. `check_access` now asks Slack in parallel: median about 270 ms, down from 740 ms.
- **Next:** Google Drive setup (in progress) and the Drive connector; install bge-m3 and run it once; do scripted event `e2` by hand on real Slack.
- **Decided:** Sam gets no Slack account (free Slack has no guests). Fixtures, Slack fake, tests and docs updated to match. Jordan joins Slack as a full member in no channels.
- **Blockers:** The golden cases cite fixture IDs while real Slack has its own; `evals/` needs the seed manifest (for C). `chunks` schema still to be agreed with B.

## Sun 4 Oct (Day 3)
- **Done:** ingestion service in `connectors/ingestion/` (all four change types, cursor per source, ACL snapshots, freshness lag), tested on the Confluence and Jira simulators and on Postgres (PR #5). Outbox of permission events for the audit service (PR #6). Identity mapping loader and the Company A seed doc, `docs/company-a-seed.md` (PR #7). Check #5 result written; workstream checklist ticked. Earlier: contract 0.2, both simulators, code brought to 0.2 (PRs #1 to #4).
- **Next:** Slack and Google accounts (in progress); Slack connector, then Drive; install bge-m3 and run it once.
- **Decided for B (Ananya unavailable):** permission events go to an outbox table, `ingestion_events`, that the audit service reads; ingestion does not write `audit_events`. Ingestion's own tables stay in `connectors/ingestion/schema.sql`, outside `db/init.sql`. Reference: `connectors/ingestion/README.md`.
- **Blockers:** `chunks` schema still to be agreed with B.

## Fri 2 Oct (Day 1)
- **Done:** repo scaffold and docs.
- **Next:** accounts for Slack and Google; check Slack internal-app limits; decide the Atlassian approach.
- **Blockers:** none yet.

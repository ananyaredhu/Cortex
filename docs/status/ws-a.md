# Status · Workstream A (Sources and freshness)

Update at the end of each day. Newest first. Keep it short: done, next, blockers.

## Sun 4 Oct (Day 3)
- **Done:** ingestion service in `connectors/ingestion/` (all four change types, cursor per source, ACL snapshots, freshness lag), tested on the Confluence and Jira simulators and on Postgres (PR #5). Outbox of permission events for the audit service (PR #6). Identity mapping loader and the Company A seed doc, `docs/company-a-seed.md` (PR #7). Check #5 result written; workstream checklist ticked. Earlier: contract 0.2, both simulators, code brought to 0.2 (PRs #1 to #4).
- **Next:** Slack and Google accounts (in progress); Slack connector, then Drive; install bge-m3 and run it once.
- **Decided for B (Ananya unavailable):** permission events go to an outbox table, `ingestion_events`, that the audit service reads; ingestion does not write `audit_events`. Ingestion's own tables stay in `connectors/ingestion/schema.sql`, outside `db/init.sql`. Reference: `connectors/ingestion/README.md`.
- **Blockers:** `chunks` schema still to be agreed with B.

## Fri 2 Oct (Day 1)
- **Done:** repo scaffold and docs.
- **Next:** accounts for Slack and Google; check Slack internal-app limits; decide the Atlassian approach.
- **Blockers:** none yet.

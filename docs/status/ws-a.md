# Status · Workstream A (Sources and freshness)

Update at the end of each day. Newest first. Keep it short: done, next, blockers.

## Sun 4 Oct (Day 3)
- **Done:** ingestion service in `connectors/ingestion/` (all four change types, cursor per source, ACL snapshots, freshness lag), tested on the Confluence and Jira simulators and on Postgres. Earlier: contract 0.2, both simulators, code brought to 0.2 (PRs #1 to #4).
- **Next:** install bge-m3 and run it once; Slack and Google accounts; Slack and Drive connectors; identity mapping.
- **Blockers:** need from B how the audit service wants `acl_change_observed` (ingestion hands `principal_change` to a sink and does not write `audit_events`); `chunks` schema still to be agreed, and ingestion adds its own `ingestion_cursors` table outside `db/init.sql`.

## Fri 2 Oct (Day 1)
- **Done:** repo scaffold and docs.
- **Next:** accounts for Slack and Google; check Slack internal-app limits; decide the Atlassian approach.
- **Blockers:** none yet.

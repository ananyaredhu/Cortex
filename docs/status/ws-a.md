# Status · Workstream A (Sources and freshness)

Update at the end of each day. Newest first. Keep it short: done, next, blockers.

## Tue 6 Oct (Day 5)
- **Done:** PR #12 (Sam has no Slack account) and PR #13 (Drive file read through an account that can see its folder) merged. Identity mapping ticked: Slack and Drive both use it and fail closed on unmapped accounts. Check #4 result written (OAuth works; `changes.watch` still open). Checklist brought up to date.
- **Done:** bge-m3 installed and run on this laptop's CPU with `embed_check`: about 2.4 chunks/s, 1024 dimensions, peak about 1.9 GB, MIT license. Result written under check #6 (B's check). Model cache kept on D: through `HF_HOME`. The 12k-page scale corpus will take hours to embed, so pre-embed it offline.
- **Done, later:** `chunks` schema settled as connector-interface 0.3: new `source` column for per-source fan-out, rules and reference queries for B's retrieval, tested on Postgres (`test_index_reads.py`). Found on the way: a vector search with a narrow ACL returns nothing unless pgvector's iterative index scan is on, so that is now a rule. Written without B (not started yet); B reviews the PR.
- **Done, evening:** Drive seed manifest (`python -m connectors.gdrive.build_manifest`) built on real Drive: 2 folders, 2 files. Through it, real Drive answers in fixture IDs and matches the fixtures on IDs, folders, versions, readers and all 10 persona-by-file access checks. A fake Drive with its own IDs passes the shared contract tests through the manifest (`gdrive-manifest`).
- **Done, night:** manifests wired into `evals/` (`evals/ids.py`, for C to review): the golden runner translates real Slack and Drive citations back to fixture IDs, and refuses to run if a case names a document the manifests do not map, so a leak cited by real ID cannot slip past `must_not_cite`. The local manifests map every seeded Slack and Drive document.
- **Done, night:** first ingestion of all four sources into Postgres, with bge-m3: 18 documents (4 Confluence, 6 Jira, 6 Slack, 2 Drive), about 1.5 min, mostly model load. New `check_index` compares the index with the fixtures through the manifests: every document has the current ACL and a vector, readers match, and each persona's prefilter finds exactly what they may see. Second run: nothing changed or re-embedded, about 30 s of Slack and Drive polling. Day 8 "data flows from all four sources" ticked.
- **Next:** do scripted event `e2` by hand on real Slack; seed the scenario fixtures in all four sources.
- **Blockers:** none on A's side. Running the golden cases against real Slack and Drive waits on B's real `/v1/ask`.

## Mon 5 Oct (Day 4)
- **Done:** Slack connector merged (PR #9) and verified on the real "Company A" workspace: all 5 channels and 6 threads found, tokens and text match the fixtures, access matches for Priya, Dana and Maya. Check #3: exempt from the 1-request-per-minute cut. `check_access` now asks Slack in parallel: median about 270 ms, down from 740 ms.
- **Done, later the same day:** Drive connector built and tested against an in-memory Drive (`connectors/gdrive/`); it passes the shared contract tests. Not yet run on real Drive.
- **Done, evening:** Google set-up finished: OAuth client, Priya, Dana and Maya signed in. On real Drive, `check_setup` matches the fixtures and all 10 persona-by-file access checks are right (about 0.5 s each). Fixed a bug found there: a file read through an account that cannot see its folder looked out of scope (PR for `ws-a/gdrive-parent-fix`).
- **Next:** Drive seed manifest; run ingestion with `--sources ...,gdrive`; install bge-m3 and run it once; do scripted event `e2` by hand on real Slack.
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

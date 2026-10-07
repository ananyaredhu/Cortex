# Running Workstream A's services in a deployment

For C's deployment (`deploy/`). What A's side needs to run, what it needs from the host, and how to tell it is
healthy. The local development set-up is the same with fewer safeguards.

## Processes
| Process | Command | Notes |
|---|---|---|
| Postgres with pgvector | `docker compose up -d` locally; any Postgres 16 with pgvector 0.8+ in production | Schema: `db/init.sql`; ingestion adds its own tables (`connectors/ingestion/schema.sql`) at start-up |
| Confluence simulator | `uvicorn simulators.confluence.app:app --host 127.0.0.1 --port 8101` | Internal only. Set `SIM_ADMIN_TOKEN` so `/sim/admin` needs a bearer token |
| Jira simulator | `uvicorn simulators.jira.app:app --host 127.0.0.1 --port 8102` | Same as Confluence |
| Ingestion, **one process only** | `python -m connectors.ingestion --poll 600 --sources confluence,jira,slack,gdrive --slack-events --drive-webhook 8110` | The only writer to the index and the outbox: never run two. Restart it if it exits; it carries on from its cursors |

Run ingestion under a supervisor that restarts it (systemd, Docker `restart: unless-stopped`). It no longer exits
when one source fails (it backs off per source, `connectors/ingestion/README.md`, "Failures"), but a restart is
always safe: every step is idempotent and cursors are saved after each batch.

## Network
- **Inbound, one path only:** Drive push notifications. Put `https://<host>/drive/notify` in front of port 8110
  (reverse proxy), then run `python -m connectors.gdrive.watch start https://<host>/drive/notify`. Channels last
  7 days: run `watch start` again weekly (cron), or notifications stop and Drive falls back to the 10-minute poll.
  The receiver rejects anything without the channel token (`GDRIVE_WEBHOOK_TOKEN`) with a bare 404.
- **Outbound:** Slack (`slack.com`, and the Socket Mode websocket: no inbound port), Google APIs, and Hugging Face
  once, to download bge-m3 (or copy the model cache in and set `HF_HUB_OFFLINE=1`).
- **Never public:** the simulators (their admin endpoints edit permissions) and Postgres.

## Configuration (names only: values live on the host, never in git)
- `.env`: `DATABASE_URL`, `EMBEDDING_BACKEND=bge-m3`, `HF_HOME`, `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`,
  `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GDRIVE_WEBHOOK_TOKEN`, `CONFLUENCE_SIM_URL`,
  `JIRA_SIM_URL`, `SIM_ADMIN_TOKEN`.
- Files, all gitignored, copied from the machine where the accounts were signed in:
  `connectors/identity-map.local.json`, `connectors/slack/seed-manifest.local.json`,
  `connectors/gdrive/gdrive.local.json`, `connectors/gdrive/seed-manifest.local.json`,
  `connectors/gdrive/token-gdrive-*.json`. `watch-channels.local.json` is written by `watch start` on the host.
- The Slack app needs Socket Mode on and the bot events in `connectors/slack/README.md`, "Events".

## Sizing
bge-m3 runs on CPU: about 2 GB of memory, loaded once at start-up (about 90 s; ingestion warms it before the first
pass when it listens for events). Embedding is the slow part: 0.6 to 2.4 chunks/s on a laptop CPU depending on chunk
length. The Company A corpus (18 documents) is nothing; the 12k-page scale corpus is about 8 hours, so do not deploy
with it. Without embedding, ingestion writes about 12 documents/s.

## Is it healthy?
- `python -m connectors.ingestion.freshness_report`: per source, the last run, the last success, the last error
  (exception class), and freshness and pipeline lag p50/p95. The same JSON is proposed for `GET /v1/freshness`.
- `python -m connectors.ingestion.check_index`: the index matches the seeded story.
- The ingestion log: one JSON line per pass; `errors` and `backing_off_seconds` name a source in trouble.

## Before a demo
```
python -m connectors.reset_demo            # what differs from the seeded story, and what to fix by hand
python -m connectors.reset_demo --apply    # reset the simulators, rebuild the Drive manifest, re-ingest, check
```
Real Slack and Drive cannot be reset from code (read-only grants): the first command lists the hand steps, such as
re-inviting Priya to `#auth-private` before scenario 4.

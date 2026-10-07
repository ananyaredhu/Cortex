# Ingestion service

Reads each connector's change feed and keeps the index in Postgres in step with the sources. Workstream A
writes it; Workstream B's retrieval, policy plane and audit service read what it leaves behind. This page is
the reference for that hand-over.

## Running it
```
python -m connectors.ingestion --once              # drain every source once
python -m connectors.ingestion --poll 5            # keep going, 5 seconds between passes
python -m connectors.ingestion --once --recrawl    # forget cursors and crawl again (rarely needed: see "Failures")
python -m connectors.ingestion --once --sources confluence,jira,slack,gdrive   # all four (Slack and Drive are real)
python -m connectors.ingestion.check_index         # compare the index with the fixtures, through the seed manifests
python -m connectors.ingestion.freshness_report    # lag per source (see "Freshness")
python -m connectors.ingestion.revocation_timing   # revocation-to-enforcement, A's stages (see the module docstring)
python -m connectors.reset_demo [--apply]          # back to the seeded story before a demo; deployment: connectors/DEPLOY.md
```
With `--poll`, add `--drive-webhook PORT` to have Drive push notifications wake ingestion early (`connectors/gdrive/README.md`, "Push notifications"), and `--slack-events` to have Slack events through Socket Mode do the same for Slack (`connectors/slack/README.md`, "Events").
The simulators must be running for Confluence and Jira (`make sim-confluence`, `make sim-jira`).
`check_index` checks, read-only, that every seeded document is indexed with the current ACL snapshot and a vector
from `EMBEDDING_BACKEND`, that the right personas can read it, and that each persona's prefilter finds exactly what
they may see. It prints fixture IDs, persona names and counts only.

First run on all four sources (6 Oct, this laptop): 18 documents, 18 chunks, bge-m3, about 1.5 minutes, most of it
loading the model. A second run changes nothing and embeds nothing; it takes about 30 seconds, the cost of polling
Slack and Drive.
Environment: `DATABASE_URL`, `EMBEDDING_BACKEND` (`bge-m3`, or `none` for no vectors), `CONFLUENCE_SIM_URL`,
`JIRA_SIM_URL`. `.env` is not loaded automatically. Hosted embedding backends are refused: document text is
only embedded locally.

## What it writes
The database applies `db/init.sql` only when its volume is first created. After a change to that file, rebuild the
local database with `docker compose down -v` then `docker compose up -d`, and re-run ingestion with `--recrawl`.

| Table | Declared in | What B can rely on |
|---|---|---|
| `documents` | `db/init.sql` | One row per document. A deleted document stays as a tombstone with `deleted = true` |
| `chunks` | `db/init.sql` | The indexed passages. `chunk_id` is `<doc_id>#<position>`. `acl_tokens` and `acl_snapshot_hash` are the document's current ACL. A deleted document has **no chunks**, so retrieval does not need to look at `documents.deleted`. `embedding` is `NULL` when `EMBEDDING_BACKEND=none`. `source` is filled in by the database from `doc_id`. How to query it: "Reading the index" in `docs/02-contracts/connector-interface.md` |
| `acl_snapshots` | `db/init.sql` | The document's ACL over time. Exactly one row per live document has `valid_to IS NULL`. `valid_from` and `valid_to` are ingestion's clock, not the connector's `observed_at` |
| `ingestion_events` | `schema.sql` here | The outbox of permission events, below |
| `ingestion_cursors` | `schema.sql` here | Ingestion's own state. Nothing else should read it |
| `ingestion_lag` | `schema.sql` here | One freshness sample per change that altered the index. See "Freshness" |
| `ingestion_sources` | `schema.sql` here | The last pass over each source: when, the last success, the last error (exception class only) |

The two tables in `schema.sql` are created at start-up if missing. They are kept out of `db/init.sql` so
that file did not need to change; they can move there whenever the shared schema is next edited.

## The outbox: `ingestion_events`
Ingestion never writes `audit_events`: the hash chain has one writer, B's audit service. Instead it appends
permission events here, and B reads them.

| Column | Meaning |
|---|---|
| `seq` | Strictly increasing. The consumer's bookmark |
| `kind` | `principal_change` or `acl_change` (the two kinds in `docs/02-contracts/acl-model.md`) |
| `source` | `slack`, `gdrive`, `confluence` or `jira` |
| `principal`, `token` | `principal_change` only: `user:<canonical email>` and the token gained or lost |
| `doc_id`, `snapshot_hash` | `acl_change` only: the document and the hash of its new ACL (the open `acl_snapshots` row) |
| `detected_at`, `observed_at` | When the connector saw the change, and when ingestion processed it |

How to consume it:
```sql
SELECT * FROM ingestion_events WHERE seq > :last_seq_handled ORDER BY seq LIMIT 100;
```
From Python, `PostgresStore.events_after(seq, limit)` does the same and returns `IngestionEvent` objects.

For each event the consumer should:
- `principal_change`: drop the cached identity and cached decisions for `principal`.
- `acl_change`: drop cached decisions, answers and other derived artifacts that involve `doc_id`.
- Either kind: write its own `acl_change_observed` audit event, then store `seq` as handled.

Guarantees and limits:
- **At least once.** After a crash an event can appear twice with different `seq`. Dropping a cache twice is harmless.
- **Order** is the order ingestion processed changes, per source.
- An `acl_change` event is written just before the index rewrite, so for a moment `chunks` may still hold the old
  tokens. The live `check_access` is authoritative either way.
- A membership change does not say whether the token was gained or lost. Re-resolve the identity to find out.
- A **delete** emits nothing. The chunks are gone; `documents.deleted` and the closed snapshot record it.
- The first time a document is indexed emits nothing: only changes to an already-indexed ACL do.
- There is one writer (one ingestion process). With several writers `seq` order would no longer be commit order.
- Rows are never removed. Trimming old rows is not built.

## Freshness
Two lags, both in seconds (`freshness.py`):

| Metric | From | To | Counts |
|---|---|---|---|
| `freshness_lag_seconds` | the edit, as the source dates it (`Document.updated_at`) | the index | New content found incrementally, and only when the source's time moved forward |
| `pipeline_lag_seconds` | the connector seeing the change | the index | Every change that altered the index: content, ACL rewrites, deletes, membership changes |

Freshness is the SLA metric ("a content edit appears in the index within 5 minutes"). It leaves out:
- **a full crawl:** an old document would count as years of lag;
- **a re-embed for a new model:** same version;
- **an edit whose source time did not move forward:** deleting a Slack reply takes the thread's time back to an
  older message, and a Google Doc's modified time stays put for later edits in one editing session.

These still count in the pipeline lag. Pipeline lag leaves out the poll interval, because a polled source sees a
change only at its next scan.

Each sample also records what started the pass: `poll`, `event` (a Slack event or Drive notification) or `crawl`.
```
python -m connectors.ingestion.freshness_report                 # JSON, the last 24 hours
python -m connectors.ingestion.freshness_report --since 2026-10-06T14:00:00Z
```
The report is the response proposed for `GET /v1/freshness` (`docs/02-contracts/api.md`). B's API can call
`connectors.ingestion.freshness_report.report(store)` or read the two tables directly.
```json
{"as_of": "...", "window_hours": 24, "sources": {"slack": {
  "last_run_at": "...", "last_ok_at": "...", "last_error": null,
  "freshness_lag_seconds": {"count": 3, "p50": 7.6, "p95": 14.9, "max": 14.9},
  "pipeline_lag_seconds": {"count": 5, "p50": 0.9, "p95": 9.9, "max": 9.9},
  "by_trigger": {"event": {"freshness_lag_seconds": {}, "pipeline_lag_seconds": {}}, "poll": {}}}}}
```
Percentiles are nearest-rank over the samples in the window. `last_error` is an exception class and never a
message, which could hold IDs. The printed run report has both lags too, for this process only.

## How changes are applied
| Change | What ingestion does |
|---|---|
| `upsert`, `acl_change` | Fetches the document. Nothing if version, embedding model and ACL all match the index. A token rewrite without re-embedding if only the ACL differs. Otherwise replaces the chunks |
| `delete`, or `DocumentNotFound` on fetch | Removes the chunks, sets the tombstone, closes the ACL snapshot |
| `principal_change` | Appends to the outbox. The index is not touched |

The cursor is saved after each batch and every step is idempotent, so a crash replays at most one batch. A full
crawl that completes in one run also removes indexed documents the crawl did not list.

## Failures
A failure in one source's pass (network, rate limit, database, outbox) stops that pass with its cursor where it
was, and nothing else:
- **The other sources carry on.** The run report lists the failed source under `errors` (the exception class only:
  messages can hold IDs and URLs), and `ingestion_sources.last_error` records it for the freshness report.
- **The failed source backs off:** 30 s, doubling to at most 15 minutes, with ±20% jitter, or longer when a rate
  limit says so (an exception with `retry_after`, such as `SlackRateLimited`). Until then, scheduled passes report
  it under `backing_off_seconds` and Slack events or Drive notifications do not wake it. The next attempt replays the
  same changes; a success resets the back-off.
- **A lost database connection** (Postgres restarted) is replaced before the next attempt.
- **An expired cursor** (`CursorExpired`: the simulator restarted or was reset, so its change log began again) makes
  ingestion clear that cursor and crawl the source again at once, removing what the crawl no longer lists. So
  `--recrawl` is no longer needed after a simulator restart.
- **`--once`** exits with status 1 when a source failed.

Below the pass, the Slack and Drive clients retry HTTP 429 (and Drive's 5xx) themselves, honouring `Retry-After`,
three times at most before the pass fails. The query path (`check_access`, `resolve_identity`) never retries: it
fails closed.

Limit, known: a document that fails on every attempt (say, one the embedder rejects) blocks its source's later
changes, retried at the back-off pace, until it is fixed or deleted. `last_error` shows it; nothing is skipped
silently, because skipping an `acl_change` would leave stale tokens in the index.

## Code map
`pipeline.py` the loop · `chunking.py` passages · `embedding.py` embedders · `store.py` the store interface and an
in-memory one · `pg_store.py` Postgres · `events.py` events and sinks · `freshness.py` and `freshness_report.py` lag samples and the report · `tests/`
run on both stores and skip Postgres when the database is down.

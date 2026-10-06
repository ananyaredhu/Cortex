# Ingestion service

Reads each connector's change feed and keeps the index in Postgres in step with the sources. Workstream A
writes it; Workstream B's retrieval, policy plane and audit service read what it leaves behind. This page is
the reference for that hand-over.

## Running it
```
python -m connectors.ingestion --once              # drain every source once
python -m connectors.ingestion --poll 5            # keep going, 5 seconds between passes
python -m connectors.ingestion --once --recrawl    # forget cursors and crawl again (after a simulator restart)
```
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

## How changes are applied
| Change | What ingestion does |
|---|---|
| `upsert`, `acl_change` | Fetches the document. Nothing if version, embedding model and ACL all match the index. A token rewrite without re-embedding if only the ACL differs. Otherwise replaces the chunks |
| `delete`, or `DocumentNotFound` on fetch | Removes the chunks, sets the tombstone, closes the ACL snapshot |
| `principal_change` | Appends to the outbox. The index is not touched |

The cursor is saved after each batch and every step is idempotent, so a crash replays at most one batch. A full
crawl that completes in one run also removes indexed documents the crawl did not list. Any failure stops that
source with its cursor unmoved; there is no retry or back-off yet.

## Code map
`pipeline.py` the loop · `chunking.py` passages · `embedding.py` embedders · `store.py` the store interface and an
in-memory one · `pg_store.py` Postgres · `events.py` events and sinks · `freshness.py` lag p50/p95 · `tests/`
run on both stores and skip Postgres when the database is down.

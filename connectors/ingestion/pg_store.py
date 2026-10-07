"""The index in Postgres with pgvector (db/init.sql), through psycopg 3."""
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from connectors.base import Document
from connectors.ingestion.events import IngestionEvent
from connectors.ingestion.freshness import LagSample, SourceRun
from connectors.ingestion.store import ChunkRow, Indexed, Snapshot, same_acl

DEFAULT_URL = "postgresql://brain:brain@localhost:5432/brain"   # the local docker-compose database
_SCHEMA = Path(__file__).with_name("schema.sql")


def _vector(embedding: list[float] | None) -> str | None:
    return None if embedding is None else "[" + ",".join(repr(float(x)) for x in embedding) + "]"


def _iso(value) -> str | None:
    return None if value is None else value.isoformat().replace("+00:00", "Z")


class PostgresStore:
    def __init__(self, conn: psycopg.Connection, *, url: str | None = None, connect_timeout: int = 5) -> None:
        """`conn` must be in autocommit mode: each write method opens its own transaction. With `url`, a connection
        broken by a database restart can be replaced (`reconnect_if_broken`)."""
        self._conn = conn
        self._url = url
        self._connect_timeout = connect_timeout
        self._conn.execute(_SCHEMA.read_text(encoding="utf-8"))

    @classmethod
    def connect(cls, url: str = DEFAULT_URL, *, connect_timeout: int = 5) -> "PostgresStore":
        return cls(psycopg.connect(url, autocommit=True, connect_timeout=connect_timeout), url=url,
                   connect_timeout=connect_timeout)

    def reconnect_if_broken(self) -> None:
        """Open a new connection when the current one is closed or broken (a database restart breaks it at the next
        query). Raises if the database is still down; the caller backs off and tries again."""
        if self._url is None or not (self._conn.closed or self._conn.broken):
            return
        self._conn = psycopg.connect(self._url, autocommit=True, connect_timeout=self._connect_timeout)

    def close(self) -> None:
        self._conn.close()

    def get_cursor(self, source: str) -> str | None:
        row = self._conn.execute("SELECT cursor FROM ingestion_cursors WHERE source = %s", (source,)).fetchone()
        return row[0] if row else None

    def set_cursor(self, source: str, cursor: str) -> None:
        self._conn.execute(
            "INSERT INTO ingestion_cursors (source, cursor) VALUES (%s, %s) "
            "ON CONFLICT (source) DO UPDATE SET cursor = EXCLUDED.cursor, updated_at = now()", (source, cursor))

    def clear_cursor(self, source: str) -> None:
        self._conn.execute("DELETE FROM ingestion_cursors WHERE source = %s", (source,))

    def live_doc_ids(self, source: str) -> set[str]:
        rows = self._conn.execute("SELECT doc_id FROM documents WHERE source = %s AND NOT deleted", (source,)).fetchall()
        return {r[0] for r in rows}

    def indexed(self, doc_id: str) -> Indexed | None:
        row = self._conn.execute("SELECT version, deleted, updated_at FROM documents WHERE doc_id = %s", (doc_id,)).fetchone()
        if row is None:
            return None
        snap = self._conn.execute(
            "SELECT snapshot_hash, tokens FROM acl_snapshots WHERE doc_id = %s AND valid_to IS NULL", (doc_id,)).fetchone()
        chunk = self._conn.execute(
            "SELECT embedding_model, embedding_version FROM chunks WHERE doc_id = %s LIMIT 1", (doc_id,)).fetchone()
        return Indexed(row[0], row[1], snap[0] if snap else None, list(snap[1]) if snap else None,
                       chunk[0] if chunk else None, chunk[1] if chunk else None, _iso(row[2]))

    def replace_document(self, doc: Document, chunks: list[ChunkRow], at: str) -> None:
        with self._conn.transaction():
            self._conn.execute(
                "INSERT INTO documents (doc_id, source, kind, title, url, parent_id, links, author, created_at, updated_at,"
                " version, deleted) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, false) "
                "ON CONFLICT (doc_id) DO UPDATE SET source = EXCLUDED.source, kind = EXCLUDED.kind, title = EXCLUDED.title,"
                " url = EXCLUDED.url, parent_id = EXCLUDED.parent_id, links = EXCLUDED.links, author = EXCLUDED.author,"
                " created_at = EXCLUDED.created_at, updated_at = EXCLUDED.updated_at, version = EXCLUDED.version,"
                " deleted = false",
                (doc.doc_id, doc.source, doc.kind, doc.title, doc.url, doc.parent_id, list(doc.links), doc.author,
                 doc.created_at or None, doc.updated_at or None, doc.version))
            self._conn.execute("DELETE FROM chunks WHERE doc_id = %s", (doc.doc_id,))
            with self._conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO chunks (chunk_id, doc_id, position, text, acl_tokens, acl_snapshot_hash, source_version,"
                    " embedding, embedding_model, embedding_version, ingested_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector, %s, %s, %s)",
                    [(c.chunk_id, c.doc_id, c.position, c.text, c.acl_tokens, c.acl_snapshot_hash, c.source_version,
                      _vector(c.embedding), c.embedding_model, c.embedding_version, c.ingested_at) for c in chunks])
            self._roll_snapshot(doc, at)

    def rewrite_acl(self, doc: Document, at: str) -> None:
        with self._conn.transaction():
            self._conn.execute("UPDATE chunks SET acl_tokens = %s, acl_snapshot_hash = %s WHERE doc_id = %s",
                               (list(doc.acl.tokens), doc.acl.snapshot_hash, doc.doc_id))
            self._roll_snapshot(doc, at)

    def delete_document(self, doc_id: str, at: str) -> None:
        with self._conn.transaction():
            self._conn.execute("DELETE FROM chunks WHERE doc_id = %s", (doc_id,))
            self._conn.execute("UPDATE documents SET deleted = true WHERE doc_id = %s", (doc_id,))
            self._conn.execute("UPDATE acl_snapshots SET valid_to = %s WHERE doc_id = %s AND valid_to IS NULL", (at, doc_id))

    def append_event(self, event: IngestionEvent) -> int:
        row = self._conn.execute(
            "INSERT INTO ingestion_events (kind, source, principal, token, doc_id, snapshot_hash, detected_at, observed_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING seq",
            (event.kind, event.source, event.principal, event.token, event.doc_id, event.snapshot_hash,
             event.detected_at, event.observed_at)).fetchone()
        return row[0]

    def events_after(self, seq: int = 0, limit: int = 100) -> list[IngestionEvent]:
        rows = self._conn.execute(
            "SELECT kind, source, detected_at, observed_at, principal, token, doc_id, snapshot_hash, seq"
            " FROM ingestion_events WHERE seq > %s ORDER BY seq LIMIT %s", (seq, limit)).fetchall()
        return [IngestionEvent(r[0], r[1], _iso(r[2]), _iso(r[3]), r[4], r[5], r[6], r[7], r[8]) for r in rows]

    def record_lag(self, samples: list[LagSample]) -> None:
        if not samples:
            return
        with self._conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO ingestion_lag (source, action, trigger, detected_at, applied_at, source_at)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                [(s.source, s.action, s.trigger, s.detected_at, s.applied_at, s.source_at) for s in samples])

    def lag_since(self, since: str) -> list[LagSample]:
        rows = self._conn.execute(
            "SELECT source, action, trigger, detected_at, applied_at, source_at FROM ingestion_lag"
            " WHERE applied_at >= %s ORDER BY applied_at, id", (since,)).fetchall()
        return [LagSample(r[0], r[1], r[2], _iso(r[3]), _iso(r[4]), _iso(r[5])) for r in rows]

    def record_run(self, run: SourceRun) -> None:
        self._conn.execute(
            "INSERT INTO ingestion_sources (source, last_run_at, last_ok_at, last_error, last_actions)"
            " VALUES (%s, %s, %s, %s, %s) ON CONFLICT (source) DO UPDATE SET last_run_at = EXCLUDED.last_run_at,"
            " last_ok_at = COALESCE(EXCLUDED.last_ok_at, ingestion_sources.last_ok_at),"
            " last_error = EXCLUDED.last_error, last_actions = EXCLUDED.last_actions",
            (run.source, run.last_run_at, run.last_ok_at, run.last_error, Jsonb(run.last_actions)))

    def runs(self) -> list[SourceRun]:
        rows = self._conn.execute(
            "SELECT source, last_run_at, last_ok_at, last_error, last_actions FROM ingestion_sources ORDER BY source").fetchall()
        return [SourceRun(r[0], _iso(r[1]), _iso(r[2]), r[3], dict(r[4])) for r in rows]

    # -- inspection (tests, debugging) ----------------------------------------------------------
    def chunks_of(self, doc_id: str) -> list[ChunkRow]:
        rows = self._conn.execute(
            "SELECT chunk_id, doc_id, position, text, acl_tokens, acl_snapshot_hash, source_version, embedding::text,"
            " embedding_model, embedding_version, ingested_at FROM chunks WHERE doc_id = %s ORDER BY position",
            (doc_id,)).fetchall()
        return [ChunkRow(r[0], r[1], r[2], r[3], list(r[4]), r[5], r[6],
                         None if r[7] is None else [float(x) for x in r[7].strip("[]").split(",")],
                         r[8], r[9], _iso(r[10])) for r in rows]

    def snapshots_of(self, doc_id: str) -> list[Snapshot]:
        rows = self._conn.execute(
            "SELECT doc_id, snapshot_hash, tokens, native, valid_from, valid_to FROM acl_snapshots"
            " WHERE doc_id = %s ORDER BY valid_from", (doc_id,)).fetchall()
        return [Snapshot(r[0], r[1], list(r[2]), r[3], _iso(r[4]), _iso(r[5])) for r in rows]

    # -- helpers --------------------------------------------------------------------------------
    def _roll_snapshot(self, doc: Document, at: str) -> None:
        current = self._conn.execute(
            "SELECT snapshot_hash, tokens FROM acl_snapshots WHERE doc_id = %s AND valid_to IS NULL", (doc.doc_id,)).fetchone()
        if current and same_acl(current[0], list(current[1]), doc):
            return
        self._conn.execute("UPDATE acl_snapshots SET valid_to = %s WHERE doc_id = %s AND valid_to IS NULL", (at, doc.doc_id))
        self._conn.execute(
            "INSERT INTO acl_snapshots (doc_id, snapshot_hash, tokens, native, valid_from) VALUES (%s, %s, %s, %s, %s)",
            (doc.doc_id, doc.acl.snapshot_hash, list(doc.acl.tokens), Jsonb(doc.acl.native), at))

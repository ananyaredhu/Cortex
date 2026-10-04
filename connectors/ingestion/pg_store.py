"""The index in Postgres with pgvector (db/init.sql), through psycopg 3."""
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from connectors.base import Document
from connectors.ingestion.events import IngestionEvent
from connectors.ingestion.store import ChunkRow, Indexed, Snapshot, same_acl

DEFAULT_URL = "postgresql://brain:brain@localhost:5432/brain"   # the local docker-compose database
_SCHEMA = Path(__file__).with_name("schema.sql")


def _vector(embedding: list[float] | None) -> str | None:
    return None if embedding is None else "[" + ",".join(repr(float(x)) for x in embedding) + "]"


def _iso(value) -> str | None:
    return None if value is None else value.isoformat().replace("+00:00", "Z")


class PostgresStore:
    def __init__(self, conn: psycopg.Connection) -> None:
        """`conn` must be in autocommit mode: each write method opens its own transaction."""
        self._conn = conn
        self._conn.execute(_SCHEMA.read_text(encoding="utf-8"))

    @classmethod
    def connect(cls, url: str = DEFAULT_URL, *, connect_timeout: int = 5) -> "PostgresStore":
        return cls(psycopg.connect(url, autocommit=True, connect_timeout=connect_timeout))

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
        row = self._conn.execute("SELECT version, deleted FROM documents WHERE doc_id = %s", (doc_id,)).fetchone()
        if row is None:
            return None
        snap = self._conn.execute(
            "SELECT snapshot_hash, tokens FROM acl_snapshots WHERE doc_id = %s AND valid_to IS NULL", (doc_id,)).fetchone()
        chunk = self._conn.execute(
            "SELECT embedding_model, embedding_version FROM chunks WHERE doc_id = %s LIMIT 1", (doc_id,)).fetchone()
        return Indexed(row[0], row[1], snap[0] if snap else None, list(snap[1]) if snap else None,
                       chunk[0] if chunk else None, chunk[1] if chunk else None)

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

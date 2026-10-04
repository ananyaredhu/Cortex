"""What ingestion needs from the index, and an in-memory implementation for tests.

The Postgres implementation is in pg_store.py. Both follow db/init.sql: `documents`, `chunks`,
bi-temporal `acl_snapshots`, plus a cursor per source.
"""
from dataclasses import dataclass
from typing import Protocol

from connectors.base import Document


@dataclass
class ChunkRow:
    """One row of `chunks` (connector-interface.md, "Chunk")."""
    chunk_id: str                      # "<doc_id>#<position>"
    doc_id: str
    position: int
    text: str
    acl_tokens: list[str]
    acl_snapshot_hash: str
    source_version: str
    embedding: list[float] | None
    embedding_model: str
    embedding_version: str
    ingested_at: str


@dataclass
class Snapshot:
    """One row of `acl_snapshots`. `valid_to` is None while it is the current ACL."""
    doc_id: str
    snapshot_hash: str
    tokens: list[str]
    native: dict
    valid_from: str
    valid_to: str | None = None


@dataclass
class Indexed:
    """What the index currently holds for a document: enough to decide whether work is needed."""
    version: str
    deleted: bool
    snapshot_hash: str | None          # of the open ACL snapshot
    acl_tokens: list[str] | None
    embedding_model: str | None        # None when the document has no chunks
    embedding_version: str | None


class Store(Protocol):
    """Each write method is atomic: it lands completely or not at all."""

    def get_cursor(self, source: str) -> str | None: ...

    def set_cursor(self, source: str, cursor: str) -> None: ...

    def clear_cursor(self, source: str) -> None:
        """Forget the cursor, so the next run is a full crawl."""

    def indexed(self, doc_id: str) -> Indexed | None: ...

    def live_doc_ids(self, source: str) -> set[str]:
        """Every document of `source` in the index that is not a tombstone."""

    def replace_document(self, doc: Document, chunks: list[ChunkRow], at: str) -> None:
        """Upsert the `documents` row, replace all its chunks, and roll the ACL snapshot if it changed."""

    def rewrite_acl(self, doc: Document, at: str) -> None:
        """Same content, new ACL: rewrite tokens and hash on the existing chunks, roll the ACL snapshot."""

    def delete_document(self, doc_id: str, at: str) -> None:
        """Remove the chunks, keep the `documents` row as a tombstone, close the open ACL snapshot."""


def same_acl(snapshot_hash: str | None, tokens: list[str] | None, doc: Document) -> bool:
    return snapshot_hash == doc.acl.snapshot_hash and tokens == list(doc.acl.tokens)


class InMemoryStore:
    """The same behaviour as PostgresStore, in dictionaries. No rollback: tests do not need it."""

    def __init__(self) -> None:
        self.cursors: dict[str, str] = {}
        self.documents: dict[str, dict] = {}
        self.chunks: dict[str, list[ChunkRow]] = {}
        self.snapshots: dict[str, list[Snapshot]] = {}

    def get_cursor(self, source: str) -> str | None:
        return self.cursors.get(source)

    def set_cursor(self, source: str, cursor: str) -> None:
        self.cursors[source] = cursor

    def clear_cursor(self, source: str) -> None:
        self.cursors.pop(source, None)

    def live_doc_ids(self, source: str) -> set[str]:
        return {d for d, row in self.documents.items() if row["source"] == source and not row["deleted"]}

    def indexed(self, doc_id: str) -> Indexed | None:
        row = self.documents.get(doc_id)
        if row is None:
            return None
        current = self._open_snapshot(doc_id)
        first = next(iter(self.chunks.get(doc_id, [])), None)
        return Indexed(row["version"], row["deleted"],
                       current.snapshot_hash if current else None, list(current.tokens) if current else None,
                       first.embedding_model if first else None, first.embedding_version if first else None)

    def replace_document(self, doc: Document, chunks: list[ChunkRow], at: str) -> None:
        self.documents[doc.doc_id] = {
            "doc_id": doc.doc_id, "source": doc.source, "kind": doc.kind, "title": doc.title, "url": doc.url,
            "parent_id": doc.parent_id, "links": list(doc.links), "author": doc.author,
            "created_at": doc.created_at, "updated_at": doc.updated_at, "version": doc.version, "deleted": False,
        }
        self.chunks[doc.doc_id] = list(chunks)
        self._roll_snapshot(doc, at)

    def rewrite_acl(self, doc: Document, at: str) -> None:
        for chunk in self.chunks.get(doc.doc_id, []):
            chunk.acl_tokens = list(doc.acl.tokens)
            chunk.acl_snapshot_hash = doc.acl.snapshot_hash
        self._roll_snapshot(doc, at)

    def delete_document(self, doc_id: str, at: str) -> None:
        self.chunks.pop(doc_id, None)
        if doc_id in self.documents:
            self.documents[doc_id]["deleted"] = True
        current = self._open_snapshot(doc_id)
        if current:
            current.valid_to = at

    # -- inspection (tests, debugging) ----------------------------------------------------------
    def chunks_of(self, doc_id: str) -> list[ChunkRow]:
        return sorted(self.chunks.get(doc_id, []), key=lambda c: c.position)

    def snapshots_of(self, doc_id: str) -> list[Snapshot]:
        return list(self.snapshots.get(doc_id, []))

    # -- helpers --------------------------------------------------------------------------------
    def _open_snapshot(self, doc_id: str) -> Snapshot | None:
        return next((s for s in self.snapshots.get(doc_id, []) if s.valid_to is None), None)

    def _roll_snapshot(self, doc: Document, at: str) -> None:
        current = self._open_snapshot(doc.doc_id)
        if current and same_acl(current.snapshot_hash, current.tokens, doc):
            return
        if current:
            current.valid_to = at
        self.snapshots.setdefault(doc.doc_id, []).append(
            Snapshot(doc.doc_id, doc.acl.snapshot_hash, list(doc.acl.tokens), doc.acl.native, at))

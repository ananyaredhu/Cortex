"""Readers of the index: Postgres (`chunks`, `documents` from db/init.sql) and an in-memory twin over A's
`InMemoryStore`, so the pipeline's tests run without a database.

Every read carries the ACL prefilter `acl_tokens && asker_tokens` (contract rule 1). The vector leg runs inside a
transaction with `SET LOCAL hnsw.iterative_scan = relaxed_order` and is re-sorted (rule 3). The keyword leg ORs the
question's terms (a natural-language question rarely contains every term of a passage, which is what
`websearch_to_tsquery` would require) and ranks by how many distinct terms a passage matches.
"""
import math
import re
from dataclasses import dataclass
from typing import Protocol

import psycopg

from connectors.ingestion.store import ChunkRow, InMemoryStore

WORD = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class DocRow:
    doc_id: str
    source: str
    kind: str
    title: str
    url: str
    parent_id: str | None
    links: tuple[str, ...]
    author: str | None
    updated_at: str | None
    version: str
    deleted: bool = False


@dataclass(frozen=True)
class Hit:
    chunk_id: str
    doc_id: str
    score: float          # keyword: distinct terms matched; vector: cosine distance (lower is better)


class IndexReader(Protocol):
    def vector_search(self, source: str, vector: list[float], model: str, tokens: list[str], k: int) -> list[Hit]: ...
    def keyword_search(self, source: str, terms: list[str], tokens: list[str], k: int) -> list[Hit]: ...
    def document(self, doc_id: str) -> DocRow | None: ...
    def chunks_of(self, doc_id: str) -> list[ChunkRow]: ...
    def documents_visible(self, tokens: list[str], source: str | None = None, limit: int = 200) -> list[DocRow]: ...


def _stem(word: str) -> str:
    for suffix in ("ing", "ies", "ed", "es", "s"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return word


# ---------------------------------------------------------------------------------------------------
class MemoryIndex:
    def __init__(self, store: InMemoryStore) -> None:
        self._store = store

    def _rows(self, source: str | None, tokens: list[str]) -> list[ChunkRow]:
        held = set(tokens)
        return [c for doc_id, chunks in self._store.chunks.items() for c in chunks
                if (source is None or doc_id.split(":", 1)[0] == source) and held & set(c.acl_tokens)]

    def vector_search(self, source: str, vector: list[float], model: str, tokens: list[str], k: int) -> list[Hit]:
        hits = []
        for c in self._rows(source, tokens):
            if c.embedding is None or c.embedding_model != model:
                continue
            dot = sum(a * b for a, b in zip(vector, c.embedding, strict=False))
            norm = math.sqrt(sum(a * a for a in vector)) * math.sqrt(sum(b * b for b in c.embedding)) or 1.0
            hits.append(Hit(c.chunk_id, c.doc_id, 1.0 - dot / norm))
        return sorted(hits, key=lambda h: h.score)[:k]

    def keyword_search(self, source: str, terms: list[str], tokens: list[str], k: int) -> list[Hit]:
        stems = {_stem(t) for t in terms}
        hits = []
        for c in self._rows(source, tokens):
            words = {_stem(w) for w in WORD.findall(c.text.lower())}
            matched = len(stems & words)
            if matched:
                hits.append(Hit(c.chunk_id, c.doc_id, float(matched)))
        return sorted(hits, key=lambda h: (-h.score, h.chunk_id))[:k]

    def document(self, doc_id: str) -> DocRow | None:
        row = self._store.documents.get(doc_id)
        if row is None:
            return None
        return DocRow(row["doc_id"], row["source"], row["kind"], row["title"], row["url"], row["parent_id"],
                      tuple(row["links"]), row["author"], row["updated_at"], row["version"], row["deleted"])

    def chunks_of(self, doc_id: str) -> list[ChunkRow]:
        return self._store.chunks_of(doc_id)

    def documents_visible(self, tokens: list[str], source: str | None = None, limit: int = 200) -> list[DocRow]:
        seen = {c.doc_id for c in self._rows(source, tokens)}
        rows = [self.document(d) for d in sorted(seen)]
        return sorted([r for r in rows if r], key=lambda r: r.updated_at or "", reverse=True)[:limit]


# ---------------------------------------------------------------------------------------------------
VECTOR_SQL = """
WITH relaxed AS MATERIALIZED (
    SELECT chunk_id, doc_id, embedding <=> %(q)s::vector AS distance
    FROM chunks
    WHERE acl_tokens && %(tokens)s::text[] AND embedding_model = %(model)s AND embedding IS NOT NULL
      AND source = %(source)s
    ORDER BY distance
    LIMIT %(k)s
)
SELECT chunk_id, doc_id, distance FROM relaxed ORDER BY distance
"""
KEYWORD_SQL = """
SELECT chunk_id, doc_id,
       (SELECT count(*) FROM unnest(%(terms)s::text[]) AS t WHERE tsv @@ plainto_tsquery('english', t)) AS matched,
       ts_rank_cd(tsv, to_tsquery('english', %(orq)s)) AS rank
FROM chunks
WHERE tsv @@ to_tsquery('english', %(orq)s) AND acl_tokens && %(tokens)s::text[] AND source = %(source)s
ORDER BY matched DESC, rank DESC, chunk_id
LIMIT %(k)s
"""
DOC_COLUMNS = "doc_id, source, kind, title, url, parent_id, links, author, updated_at, version, deleted"


class PostgresIndex:
    def __init__(self, conn: psycopg.Connection) -> None:
        """`conn` in autocommit mode; the vector leg opens its own transaction for the SET LOCAL."""
        self._conn = conn

    @classmethod
    def connect(cls, url: str) -> "PostgresIndex":
        return cls(psycopg.connect(url, autocommit=True, connect_timeout=5))

    def vector_search(self, source: str, vector: list[float], model: str, tokens: list[str], k: int) -> list[Hit]:
        q = "[" + ",".join(f"{x:.7f}" for x in vector) + "]"
        with self._conn.transaction():
            self._conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
            rows = self._conn.execute(VECTOR_SQL, {"q": q, "tokens": list(tokens), "model": model, "source": source,
                                                   "k": k}).fetchall()
        return [Hit(r[0], r[1], float(r[2])) for r in rows]

    def keyword_search(self, source: str, terms: list[str], tokens: list[str], k: int) -> list[Hit]:
        terms = [t for t in terms if WORD.fullmatch(t)]
        if not terms:
            return []
        rows = self._conn.execute(KEYWORD_SQL, {"terms": terms, "orq": " | ".join(terms), "tokens": list(tokens),
                                                "source": source, "k": k}).fetchall()
        return [Hit(r[0], r[1], float(r[2])) for r in rows]

    @staticmethod
    def _doc(r) -> DocRow:
        return DocRow(r[0], r[1], r[2], r[3], r[4], r[5], tuple(r[6] or ()), r[7], _iso(r[8]), r[9], bool(r[10]))

    def document(self, doc_id: str) -> DocRow | None:
        r = self._conn.execute(f"SELECT {DOC_COLUMNS} FROM documents WHERE doc_id = %s", (doc_id,)).fetchone()
        return self._doc(r) if r else None

    def chunks_of(self, doc_id: str) -> list[ChunkRow]:
        rows = self._conn.execute(
            "SELECT chunk_id, doc_id, position, text, acl_tokens, acl_snapshot_hash, source_version, embedding_model,"
            " embedding_version, ingested_at FROM chunks WHERE doc_id = %s ORDER BY position", (doc_id,)).fetchall()
        return [ChunkRow(r[0], r[1], r[2], r[3], list(r[4]), r[5], r[6], None, r[7], r[8], _iso(r[9]) or "") for r in rows]

    def documents_visible(self, tokens: list[str], source: str | None = None, limit: int = 200) -> list[DocRow]:
        rows = self._conn.execute(
            f"SELECT {DOC_COLUMNS} FROM documents d WHERE NOT deleted AND (%(source)s::text IS NULL OR source = %(source)s)"
            " AND EXISTS (SELECT 1 FROM chunks c WHERE c.doc_id = d.doc_id AND c.acl_tokens && %(tokens)s::text[])"
            " ORDER BY updated_at DESC NULLS LAST LIMIT %(limit)s",
            {"source": source, "tokens": list(tokens), "limit": limit}).fetchall()
        return [self._doc(r) for r in rows]


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")

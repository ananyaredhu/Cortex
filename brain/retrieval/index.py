"""Readers of the index: Postgres (`chunks`, `documents` from db/init.sql) and an in-memory twin over A's
`InMemoryStore`, so the pipeline's tests run without a database.

Every read carries the ACL prefilter `acl_tokens && asker_tokens` (contract rule 1). The vector leg runs inside a
transaction with `SET LOCAL hnsw.iterative_scan = relaxed_order` and is re-sorted (rule 3). The keyword leg ORs the
question's terms (a natural-language question rarely contains every term of a passage, which is what
`websearch_to_tsquery` would require) and ranks by how many distinct terms a passage matches.
"""
import math
import re
import threading
from dataclasses import dataclass
from typing import Protocol

import psycopg

from connectors.ingestion.store import ChunkRow, InMemoryStore

WORD = re.compile(r"[a-z0-9]+")
TITLE_TERM = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")        # a title term may be a ticket key: dbmig-150


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
    fields: tuple[tuple[str, str], ...] = ()      # Document.fields as sorted pairs (the row is frozen); `dict(row.fields)`


@dataclass(frozen=True)
class Hit:
    chunk_id: str
    doc_id: str
    score: float          # keyword: distinct terms matched; vector: cosine distance (lower is better)


class IndexReader(Protocol):
    def vector_search(self, source: str, vector: list[float], model: str, tokens: list[str], k: int) -> list[Hit]: ...
    def keyword_search(self, source: str, terms: list[str], tokens: list[str], k: int) -> list[Hit]: ...
    def title_search(self, source: str, terms: list[str], tokens: list[str], k: int) -> list[Hit]: ...
    def document(self, doc_id: str) -> DocRow | None: ...
    def chunks_of(self, doc_id: str) -> list[ChunkRow]: ...
    def documents_visible(self, tokens: list[str], source: str | None = None, limit: int = 200) -> list[DocRow]: ...
    def documents_hidden(self, tokens: list[str]) -> list[DocRow]: ...
    def parents_visible(self, tokens: list[str]) -> set[str]: ...
    def documents_under(self, parents: list[str], tokens: list[str], limit: int = 10) -> list[DocRow]: ...
    def documents_all(self) -> list[DocRow]: ...


def _title_match(term: str, stems: set[str], words: set[str]) -> bool:
    """A plain term matches a title word; a key like `dbmig-150` needs every part of it in the title."""
    if "-" in term:
        return all(_stem(part) in words for part in term.split("-"))
    return _stem(term) in words and _stem(term) in stems


def _stem(word: str) -> str:
    for suffix in ("ing", "ies", "ed", "es", "s"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return word


# ---------------------------------------------------------------------------------------------------
class MemoryIndex:
    def __init__(self, store: InMemoryStore) -> None:
        self._store = store
        self.title_leg = True

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

    def title_search(self, source: str, terms: list[str], tokens: list[str], k: int) -> list[Hit]:
        """Documents whose title holds the terms (ticket keys, names): a document's title is not in its chunks."""
        stems = {_stem(t) for t in terms}
        held = {c.doc_id: c for c in self._rows(source, tokens)}
        terms = [t for t in terms if TITLE_TERM.fullmatch(t)]
        hits = []
        for doc_id, chunk in held.items():
            row = self._store.documents.get(doc_id)
            if row is None or row.get("deleted"):
                continue
            words = {_stem(w) for w in WORD.findall((row["title"] or "").lower())}
            matched = sum((2 if "-" in t else 1) for t in terms if _title_match(t, stems, words))
            if matched:
                first = min((c for c in self._store.chunks_of(doc_id)), key=lambda c: c.position, default=chunk)
                hits.append(Hit(first.chunk_id, doc_id, float(matched)))
        return sorted(hits, key=lambda h: (-h.score, h.doc_id))[:k]

    def document(self, doc_id: str) -> DocRow | None:
        row = self._store.documents.get(doc_id)
        if row is None:
            return None
        return DocRow(row["doc_id"], row["source"], row["kind"], row["title"], row["url"], row["parent_id"],
                      tuple(row["links"]), row["author"], row["updated_at"], row["version"], row["deleted"],
                      tuple(sorted((row.get("fields") or {}).items())))

    def chunks_of(self, doc_id: str) -> list[ChunkRow]:
        return self._store.chunks_of(doc_id)

    def documents_visible(self, tokens: list[str], source: str | None = None, limit: int = 200) -> list[DocRow]:
        seen = {c.doc_id for c in self._rows(source, tokens)}
        rows = [self.document(d) for d in sorted(seen)]
        return sorted([r for r in rows if r], key=lambda r: r.updated_at or "", reverse=True)[:limit]

    def parents_visible(self, tokens: list[str]) -> set[str]:
        """The containers (`jira:DBMIG`, `slack:C_DBMIG`, ...) holding at least one document the tokens reach."""
        return {r.parent_id for r in self.documents_visible(tokens, limit=10_000) if r.parent_id}

    def documents_under(self, parents: list[str], tokens: list[str], limit: int = 10) -> list[DocRow]:
        """Documents in the given containers that the tokens reach, newest first."""
        wanted = set(parents)
        return [r for r in self.documents_visible(tokens, limit=10_000) if r.parent_id in wanted][:limit]

    def documents_all(self) -> list[DocRow]:
        """Every live document, for administrators' tools. Nothing the asker-facing code calls."""
        rows = [self.document(d) for d in sorted(self._store.documents)]
        return [r for r in rows if r and not r.deleted]

    def documents_hidden(self, tokens: list[str]) -> list[DocRow]:
        """Every live document no chunk of which carries one of `tokens`: what the prefilter keeps from the asker (fix F5)."""
        seen = {c.doc_id for c in self._rows(None, tokens)}
        rows = [self.document(d) for d in sorted(self._store.documents) if d not in seen]
        return [r for r in rows if r and not r.deleted]


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
       ts_rank_cd(tsv, to_tsquery('english', %(orq)s), %(norm)s) AS rank
FROM chunks
WHERE tsv @@ to_tsquery('english', %(orq)s) AND acl_tokens && %(tokens)s::text[] AND source = %(source)s
ORDER BY matched DESC, rank DESC, chunk_id
LIMIT %(k)s
"""
TITLE_SQL = """
SELECT (SELECT c.chunk_id FROM chunks c WHERE c.doc_id = d.doc_id ORDER BY c.position LIMIT 1) AS chunk_id, d.doc_id,
       (SELECT coalesce(sum(CASE WHEN t LIKE '%%-%%' THEN 2 ELSE 1 END), 0) FROM unnest(%(terms)s::text[]) AS t
         WHERE to_tsvector('english', d.title) @@ plainto_tsquery('english', t)) AS matched
FROM documents d
WHERE NOT d.deleted AND d.source = %(source)s AND to_tsvector('english', d.title) @@ to_tsquery('english', %(orq)s)
  AND EXISTS (SELECT 1 FROM chunks c WHERE c.doc_id = d.doc_id AND c.acl_tokens && %(tokens)s::text[])
ORDER BY matched DESC, d.doc_id
LIMIT %(k)s
"""
DOC_COLUMNS = "doc_id, source, kind, title, url, parent_id, links, author, updated_at, version, deleted, fields"


class PostgresIndex:
    def __init__(self, conn: psycopg.Connection) -> None:
        """`conn` in autocommit mode; the vector leg opens its own transaction for the SET LOCAL.

        One connection serves every request thread. psycopg serializes plain statements itself, but a transaction
        block entered from two threads at once is OutOfOrderTransactionNesting, so the vector leg takes a lock
        (concurrent asks, for example the split-screen, otherwise fail at the retrieve stage).
        """
        self._conn = conn
        self._lock = threading.Lock()
        self.rank_normalization = 0          # ts_rank_cd's normalization bit mask; 2 (divide by length) measured no gain with the title leg
        self.title_leg = True                # also search titles, which are not in the chunks

    @classmethod
    def connect(cls, url: str) -> "PostgresIndex":
        return cls(psycopg.connect(url, autocommit=True, connect_timeout=5))

    def vector_search(self, source: str, vector: list[float], model: str, tokens: list[str], k: int) -> list[Hit]:
        q = "[" + ",".join(f"{x:.7f}" for x in vector) + "]"
        with self._lock, self._conn.transaction():
            self._conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
            rows = self._conn.execute(VECTOR_SQL, {"q": q, "tokens": list(tokens), "model": model, "source": source,
                                                   "k": k}).fetchall()
        return [Hit(r[0], r[1], float(r[2])) for r in rows]

    def keyword_search(self, source: str, terms: list[str], tokens: list[str], k: int) -> list[Hit]:
        terms = [t for t in terms if WORD.fullmatch(t)]
        if not terms:
            return []
        rows = self._conn.execute(KEYWORD_SQL, {"terms": terms, "orq": " | ".join(terms), "tokens": list(tokens),
                                                "source": source, "k": k, "norm": self.rank_normalization}).fetchall()
        return [Hit(r[0], r[1], float(r[2])) for r in rows]

    def title_search(self, source: str, terms: list[str], tokens: list[str], k: int) -> list[Hit]:
        terms = [t for t in terms if TITLE_TERM.fullmatch(t)]
        if not terms:
            return []
        rows = self._conn.execute(TITLE_SQL, {"terms": terms, "orq": " | ".join(terms), "tokens": list(tokens), "source": source,
                                              "k": k}).fetchall()
        return [Hit(r[0], r[1], float(r[2])) for r in rows if r[0]]

    @staticmethod
    def _doc(r) -> DocRow:
        return DocRow(r[0], r[1], r[2], r[3], r[4], r[5], tuple(r[6] or ()), r[7], _iso(r[8]), r[9], bool(r[10]),
                      tuple(sorted((str(k), str(v)) for k, v in (r[11] or {}).items())))

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

    def parents_visible(self, tokens: list[str]) -> set[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT parent_id FROM documents d WHERE NOT deleted AND parent_id IS NOT NULL AND EXISTS"
            " (SELECT 1 FROM chunks c WHERE c.doc_id = d.doc_id AND c.acl_tokens && %(tokens)s::text[])",
            {"tokens": list(tokens)}).fetchall()
        return {r[0] for r in rows}

    def documents_under(self, parents: list[str], tokens: list[str], limit: int = 10) -> list[DocRow]:
        rows = self._conn.execute(
            f"SELECT {DOC_COLUMNS} FROM documents d WHERE NOT deleted AND parent_id = ANY(%(parents)s::text[]) AND EXISTS"
            " (SELECT 1 FROM chunks c WHERE c.doc_id = d.doc_id AND c.acl_tokens && %(tokens)s::text[])"
            " ORDER BY updated_at DESC NULLS LAST LIMIT %(limit)s",
            {"parents": list(parents), "tokens": list(tokens), "limit": limit}).fetchall()
        return [self._doc(r) for r in rows]

    def documents_all(self) -> list[DocRow]:
        rows = self._conn.execute(f"SELECT {DOC_COLUMNS} FROM documents WHERE NOT deleted ORDER BY doc_id").fetchall()
        return [self._doc(r) for r in rows]

    def documents_hidden(self, tokens: list[str]) -> list[DocRow]:
        rows = self._conn.execute(
            f"SELECT {DOC_COLUMNS} FROM documents d WHERE NOT deleted AND NOT EXISTS"
            " (SELECT 1 FROM chunks c WHERE c.doc_id = d.doc_id AND c.acl_tokens && %(tokens)s::text[]) ORDER BY doc_id",
            {"tokens": list(tokens)}).fetchall()
        return [self._doc(r) for r in rows]


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")

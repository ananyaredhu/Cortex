"""Per-source fan-out, two legs, reciprocal-rank fusion, grouped by document.

Both legs are prefiltered on the asker's tokens, so the candidates are already plausible; the PDP's live check
still runs on every one of them (the prefilter may be stale or, by the narrowing rule, too generous).
"""
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from .index import Hit, IndexReader

WORD = re.compile(r"[a-z0-9]+")
STOP = frozenset("""
the and what whats were was there that this with from show have about which for are any our you did does can how who
when into last week what's its it's they them their been being will would should could than then also just like
""".split())
RRF_K = 60


@dataclass
class Candidate:
    doc_id: str
    source: str
    score: float = 0.0
    chunk_ids: list[str] = field(default_factory=list)
    keyword_rank: int | None = None
    vector_rank: int | None = None


def query_terms(question: str) -> list[str]:
    seen: dict[str, None] = {}
    for w in WORD.findall(question.lower()):
        if len(w) >= 3 and w not in STOP:
            seen.setdefault(w, None)
    return list(seen)


def hybrid_search(index: IndexReader, question: str, tokens: Iterable[str], sources: Iterable[str], *,
                  vector: list[float] | None = None, model: str | None = None, k: int = 10, candidates: int = 8,
                  max_distance: float = 0.45, min_terms: int = 2) -> list[Candidate]:
    """Top `candidates` documents across `sources`. A keyword hit needs `min_terms` distinct terms (or all of them
    for a shorter question); a vector hit needs a cosine distance under `max_distance`.

    0.45 comes from the Company A index with bge-m3 (8 Oct, all nine golden questions against all 18 documents): the
    documents a question is about sit at 0.26 to 0.44, everything else at 0.48 and above, and a question about
    nothing in the corpus has no document under 0.53 and no passage matching two of its terms."""
    tokens = sorted(set(tokens))
    terms = query_terms(question)
    need = min(min_terms, len(terms)) if terms else 1
    by_doc: dict[str, Candidate] = {}

    def fuse(hits: list[Hit], leg: str) -> None:
        rank = 0
        seen_docs: set[str] = set()
        for h in hits:
            if h.doc_id in seen_docs:
                if h.chunk_id not in by_doc[h.doc_id].chunk_ids:
                    by_doc[h.doc_id].chunk_ids.append(h.chunk_id)
                continue
            seen_docs.add(h.doc_id)
            rank += 1
            cand = by_doc.setdefault(h.doc_id, Candidate(h.doc_id, h.doc_id.split(":", 1)[0]))
            cand.score += 1.0 / (RRF_K + rank)
            if h.chunk_id not in cand.chunk_ids:
                cand.chunk_ids.append(h.chunk_id)
            setattr(cand, f"{leg}_rank", rank)

    for source in sources:
        if terms:
            fuse([h for h in index.keyword_search(source, terms, tokens, k) if h.score >= need], "keyword")
        if vector is not None and model:
            fuse([h for h in index.vector_search(source, vector, model, tokens, k) if h.score <= max_distance], "vector")
    return sorted(by_doc.values(), key=lambda c: (-c.score, c.doc_id))[:candidates]

"""Measure retrieval relevance as the corpus grows: `python -m brain.relevance.run [--sizes 0,1000,4000,12000] [--out FILE]`.

Needs the local Postgres (db-up). Writes only to a database named `brain_relevance...` (never the demo one), rebuilt each run.
"""
import argparse
import json
import os
import statistics
import time
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb  # noqa: F401  (kept for parity with the store's writes)

from brain.retrieval import PostgresIndex, hybrid_search
from connectors.ingestion import Ingestor, NullEmbedder
from connectors.ingestion.chunking import chunk_body
from connectors.ingestion.pg_store import PostgresStore
from connectors.ingestion.scratch_db import prepare
from connectors.stub.fixture_connector import FixtureConnector
from fixtures.loader import load
from simulators.scale import Scale, generate

URL = os.environ.get("RELEVANCE_DATABASE_URL", "postgresql://brain:brain@localhost:5432/brain_relevance")
SOURCES = ("confluence", "jira", "slack", "gdrive")
QUESTIONS = Path(__file__).with_name("questions.json")
CANDIDATES = 8                       # Settings.candidates: what retrieval hands to the permission check
MARK = "sha256:relevance"
VARIANTS = {                            # name -> attributes set on the index before evaluating
    "baseline": {"rank_normalization": 0, "title_leg": False},      # retrieval as it was before T10
    "title": {"rank_normalization": 0, "title_leg": True, "title_weight": 1.0},
    "title1.5": {"rank_normalization": 0, "title_leg": True, "title_weight": 1.5},
    "title2": {"rank_normalization": 0, "title_leg": True, "title_weight": 2.0},
    "title3": {"rank_normalization": 0, "title_leg": True, "title_weight": 3.0},
    "norm2": {"rank_normalization": 2, "title_leg": False},         # ts_rank_cd divided by document length
}


def corpus_rows(company, pages: int, threads: int):
    """(documents, chunks) rows for the first `pages` generated pages and `threads` threads."""
    docs, chunks = [], []
    for page in company.pages[:pages]:
        doc_id = f"confluence:{page.space}/{page.id}"
        docs.append((doc_id, "confluence", "page", page.title, f"https://scale.invalid/{doc_id}", f"confluence:{page.space}", [], None,
                     "2026-10-01T00:00:00Z", "v1"))
        for i, text in enumerate(chunk_body(page.body)):
            chunks.append((f"{doc_id}#{i}", doc_id, i, text, list(page.tokens), MARK, "v1", "none", "0"))
    tokens = company.channel_tokens()
    for n, thread in enumerate(company.threads[:threads]):
        doc_id = f"slack:{thread.channel}/t{n:05d}"
        body = "\n\n".join([thread.text] + [text for _, text in thread.replies])
        docs.append((doc_id, "slack", "thread", f"#{thread.channel} thread", f"https://scale.invalid/{doc_id}", f"slack:{thread.channel}",
                     [], thread.author, "2026-10-01T00:00:00Z", "v1"))
        for i, text in enumerate(chunk_body(body)):
            chunks.append((f"{doc_id}#{i}", doc_id, i, text, list(tokens[thread.channel]), MARK, "v1", "none", "0"))
    return docs, chunks


def insert(conn, docs, chunks) -> None:
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO documents (doc_id, source, kind, title, url, parent_id, links, author, updated_at, version, deleted)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, false) ON CONFLICT (doc_id) DO NOTHING", docs)
        cur.executemany(
            "INSERT INTO chunks (chunk_id, doc_id, position, text, acl_tokens, acl_snapshot_hash, source_version, embedding_model,"
            " embedding_version) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (chunk_id) DO NOTHING", chunks)


def asker_tokens(company, data) -> dict[str, list[str]]:
    """Company A's token set for each persona, plus the teams and channels the generator gave them."""
    out = {}
    for persona in data["personas"]:
        email = persona["tokens"][0].removeprefix("user:")
        out[persona["id"]] = sorted(set(persona["tokens"]) | company.tokens_of(email) if email.endswith("@companya.com")
                                    else set(persona["tokens"]))
    return out


def evaluate(index, questions, tokens) -> list[dict]:
    rows = []
    for q in questions:
        started = time.perf_counter()
        found = hybrid_search(index, q["question"], tokens[q["persona"]], SOURCES, candidates=CANDIDATES)
        ms = (time.perf_counter() - started) * 1000
        ranked = [c.doc_id for c in found]
        ranks = [ranked.index(d) + 1 for d in q["relevant"] if d in ranked]
        rows.append({"id": q["id"], "kind": q["kind"], "relevant": q["relevant"], "ranked": ranked, "first": min(ranks, default=0),
                     "found": len(ranks), "ms": ms})
    return rows


def summarize(rows: list[dict]) -> dict:
    n = len(rows)

    def hit(k):
        return sum(1 for r in rows if r["first"] and r["first"] <= k) / n
    recall = statistics.mean(
        sum(1 for d in r["relevant"] if d in r["ranked"][:CANDIDATES]) / len(r["relevant"]) for r in rows)
    return {"questions": n, "hit@1": hit(1), "hit@3": hit(3), "hit@8": hit(CANDIDATES), "recall@8": recall,
            "mrr": statistics.mean((1 / r["first"]) if r["first"] else 0.0 for r in rows),
            "median_ms": statistics.median(r["ms"] for r in rows), "p95_ms": sorted(r["ms"] for r in rows)[int(0.95 * (n - 1))]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sizes", default="0,1000,4000,12000", help="generated Confluence pages to add at each step")
    parser.add_argument("--out", help="write the results as JSON to this file")
    parser.add_argument("--variants", default="baseline", help="comma list of ranking variants to compare at every size, see VARIANTS")
    parser.add_argument("--reuse", action="store_true", help="evaluate the corpus already in the database, do not rebuild it")
    args = parser.parse_args()
    sizes = sorted({int(s) for s in args.sizes.split(",")})
    questions = json.loads(QUESTIONS.read_text(encoding="utf-8"))["questions"]
    data = load()
    scale = Scale(pages=max(sizes) or 1)
    company = generate(scale)
    variants = [v.strip() for v in args.variants.split(",")]
    if args.reuse:
        sizes = [sizes[-1]]
    else:
        prepare(URL, prefix="brain_relevance", reset=True)
    with psycopg.connect(URL, autocommit=True) as conn:
        index = PostgresIndex(conn)
        tokens = asker_tokens(company, data)
        if not args.reuse:
            Ingestor([FixtureConnector(s) for s in SOURCES], PostgresStore(conn), NullEmbedder()).run_once()
        report, done = [], 0
        for size in sizes:
            if not args.reuse:
                take = int(len(company.threads) * size / scale.pages) if scale.pages else 0
                docs, chunks = corpus_rows(company, size, take)
                insert(conn, [d for d in docs], chunks)
                conn.execute("ANALYZE chunks")
            else:
                take = None
            total = conn.execute("SELECT count(*) FROM documents").fetchone()[0]
            for name in variants:
                for attr, value in VARIANTS[name].items():
                    setattr(index, attr, value)
                rows = evaluate(index, questions, tokens)
                result = {"variant": name, "generated_pages": size, "generated_threads": take, "documents": total, **summarize(rows)}
                result["by_kind"] = {k: summarize([r for r in rows if r["kind"] == k]) for k in sorted({q["kind"] for q in questions})}
                result["misses"] = [{"id": r["id"], "kind": r["kind"], "first": r["first"], "found": f"{r['found']}/{len(r['relevant'])}"}
                                    for r in rows if r["found"] < len(r["relevant"])]
                report.append(result)
                done = size
                print(f"{name:>11} {total:>6} documents  hit@1 {result['hit@1']:.2f}  hit@3 {result['hit@3']:.2f}  "
                      f"hit@8 {result['hit@8']:.2f}  recall@8 {result['recall@8']:.2f}  MRR {result['mrr']:.2f}  "
                      f"median {result['median_ms']:.0f} ms  p95 {result['p95_ms']:.0f} ms  misses {len(result['misses'])}")
    print(f"\nlast size: {done} generated pages. Misses at the largest size:")
    for m in report[-1]["misses"]:
        print("  ", m)
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()

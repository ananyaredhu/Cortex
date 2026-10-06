"""Check the index in Postgres against the fixtures, read-only: is every seeded document there, with the right readers?

    python -m connectors.ingestion.check_index

For every fixture document: it has chunks; every chunk carries the document's current ACL (the open `acl_snapshots`
row) and a vector from the expected model; and the personas whose tokens overlap its ACL are the ones the fixture
says. Then, per persona, the retrieval prefilter (`acl_tokens && tokens`) returns exactly the documents that persona
may see. Real Slack and Drive IDs are translated through the seed manifests; Confluence and Jira use fixture IDs.

Prints fixture IDs, persona names and counts only: no document text and no real IDs.
"""
import os
import sys

import psycopg

from connectors.env import load_dotenv
from connectors.gdrive.manifest import SeedManifest as DriveManifest
from connectors.ingestion.pg_store import DEFAULT_URL
from connectors.slack.manifest import SeedManifest as SlackManifest
from fixtures.loader import load


class Ids:
    """Fixture <-> index IDs and tokens. Empty manifests mean the index uses the fixture IDs (simulators, fakes)."""

    def __init__(self, slack: SlackManifest, gdrive: DriveManifest) -> None:
        self.slack, self.gdrive = slack, gdrive

    def real_doc(self, doc_id: str) -> str:
        manifest = {"slack": self.slack, "gdrive": self.gdrive}.get(doc_id.split(":", 1)[0])
        return (manifest.real_doc(doc_id) if manifest else None) or doc_id

    def real_tokens(self, tokens: list[str]) -> list[str]:
        return [self.slack.real_token(t) for t in tokens]

    def fixture_tokens(self, tokens: list[str]) -> list[str]:
        return [self.slack.fixture_token(t) for t in tokens]


def _readers(data: dict, tokens: list[str]) -> list[str]:
    """Personas whose tokens overlap `tokens`. Compared instead of the token lists, which can differ and still let in
    the same people (e.g. a person also covered by their group, on Drive)."""
    return sorted(p["id"] for p in data["personas"] if set(p["tokens"]) & set(tokens))


def check(conn: psycopg.Connection, data: dict, ids: Ids, *, sources: list[str], model: str) -> tuple[list[str], list[str]]:
    """(summary lines, problems) for the fixture documents of `sources`."""
    lines, problems = [], []
    docs = [d for d in data["documents"] if d["source"] in sources]
    real_to_fixture = {ids.real_doc(d["doc_id"]): d["doc_id"] for d in docs}
    found = 0
    for d in docs:
        real = ids.real_doc(d["doc_id"])
        rows = conn.execute("SELECT acl_tokens, acl_snapshot_hash, embedding IS NOT NULL, embedding_model, source"
                            " FROM chunks WHERE doc_id = %s", (real,)).fetchall()
        if not rows:
            problems.append(f"{d['doc_id']}: not in the index")
            continue
        found += 1
        snapshot = conn.execute("SELECT snapshot_hash FROM acl_snapshots WHERE doc_id = %s AND valid_to IS NULL",
                                (real,)).fetchone()
        tokens = sorted(rows[0][0])
        if any(sorted(r[0]) != tokens or r[1] != rows[0][1] for r in rows):
            problems.append(f"{d['doc_id']}: its chunks carry different ACLs")
        if snapshot is None or snapshot[0] != rows[0][1]:
            problems.append(f"{d['doc_id']}: chunks do not carry the current ACL snapshot")
        if not all(r[2] and r[3] == model for r in rows):
            problems.append(f"{d['doc_id']}: chunks without a {model} vector")
        if any(r[4] != d["source"] for r in rows):
            problems.append(f"{d['doc_id']}: chunks.source is not {d['source']}")
        got, wanted = _readers(data, ids.fixture_tokens(tokens)), _readers(data, d["acl"]["tokens"])
        if got != wanted:
            problems.append(f"{d['doc_id']}: readable by {got or 'nobody'} in the index, the fixture has {wanted or 'nobody'}")
    for persona in data["personas"]:
        rows = conn.execute("SELECT DISTINCT doc_id FROM chunks WHERE acl_tokens && %s AND source = ANY(%s)",
                            (ids.real_tokens(persona["tokens"]), sources)).fetchall()
        seen = {real_to_fixture[r[0]] for r in rows if r[0] in real_to_fixture}
        wanted = {d["doc_id"] for d in docs if set(d["acl"]["tokens"]) & set(persona["tokens"])}
        lines.append(f"{persona['id']}: prefilter finds {len(seen)} of the {len(docs)} seeded documents")
        if seen != wanted:
            problems.append(f"{persona['id']}: prefilter finds {sorted(seen - wanted)} too many, misses {sorted(wanted - seen)}")
    counts = conn.execute("SELECT source, count(DISTINCT doc_id), count(*) FROM chunks WHERE source = ANY(%s)"
                          " GROUP BY source ORDER BY source", (sources,)).fetchall()
    extra = sum(n for _, n, _ in counts) - found
    lines.insert(0, "indexed: " + ", ".join(f"{s} {n} documents / {c} chunks" for s, n, c in counts)
                 + (f" ({extra} not in the fixtures)" if extra else ""))
    return lines, problems


def main() -> None:
    load_dotenv()
    sources = (sys.argv[1] if len(sys.argv) > 1 else "confluence,jira,slack,gdrive").split(",")
    model = os.environ.get("EMBEDDING_BACKEND") or "bge-m3"
    with psycopg.connect(os.environ.get("DATABASE_URL") or DEFAULT_URL, connect_timeout=5) as conn:
        lines, problems = check(conn, load(), Ids(SlackManifest.load(), DriveManifest.load()), sources=sources, model=model)
    for line in lines:
        print(line)
    for problem in problems:
        print("DIFFERS:", problem)
    if problems:
        raise SystemExit(1)
    print("The index matches the fixtures.")


if __name__ == "__main__":
    main()

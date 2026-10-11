"""Proposals for the project registry (backlog T7, item 2): `python -m brain.projects.suggest [--fixture]`.

An administrator's tool, run from a terminal, never reachable through the API. It reads the whole index, finds pairs of places that
look related and are not yet in one registered project, and prints them with the evidence. **Nothing is applied**: a proposal is
a suggestion for a person to read and, if right, add to `registry.json` by hand through a reviewed change.

Three signals, each weak alone:
- a Jira key (`DBMIG-142`) written in a document that lives somewhere else (a Slack thread, a page);
- documents that link to each other across two places;
- a Slack channel whose name abbreviates a Jira key (`#db-migration` and `DBMIG`).
A proposal lists the places and the kind and document of each piece of evidence, never document text.
"""
import argparse
import re
from collections import defaultdict

from brain.projects import Registry
from brain.retrieval.index import DocRow

KEY = re.compile(r"\b([A-Z][A-Z0-9]{1,9})-\d+\b")


def _owner(registry: Registry) -> dict[str, str]:
    """container -> the registered project it belongs to."""
    return {c: p.project_id for p in registry.projects.values() for c in p.containers()}


def suggest(docs: list[DocRow], texts: dict[str, str], registry: Registry) -> list[dict]:
    owner = _owner(registry)
    parents = {d.doc_id: d.parent_id for d in docs}
    found: dict[tuple[str, str], list[dict]] = defaultdict(list)

    def propose(a: str | None, b: str | None, kind: str, doc_id: str) -> None:
        if not a or not b or a == b:
            return
        if owner.get(a) and owner.get(a) == owner.get(b):
            return                                                  # already one registered project
        found[tuple(sorted((a, b)))].append({"kind": kind, "doc_id": doc_id})

    for doc in docs:
        if doc.source != "jira":
            for key in sorted(set(KEY.findall(texts.get(doc.doc_id, "")))):
                propose(doc.parent_id, f"jira:{key}", "jira_key_mentioned", doc.doc_id)
        for target in doc.links:
            propose(doc.parent_id, parents.get(target), "documents_link", doc.doc_id)
    keys = {p for p in parents.values() if p and p.startswith("jira:")}
    for doc in docs:
        match = re.match(r"#([a-z0-9][a-z0-9-]*)", doc.title or "")
        if doc.source == "slack" and match:
            compact = match.group(1).replace("-", "")
            for jira in sorted(keys):
                if len(jira) > 8 and compact.startswith(jira.split(":", 1)[1].lower()):
                    propose(doc.parent_id, jira, "name_similarity", doc.doc_id)
    proposals = [{"a": a, "b": b, "score": len(ev), "evidence": ev} for (a, b), ev in found.items()]
    for p in proposals:
        p["already_in"] = sorted({owner[x] for x in (p["a"], p["b"]) if x in owner})
    return sorted(proposals, key=lambda p: (-p["score"], p["a"], p["b"]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fixture", action="store_true", help="read the fixture corpus instead of the Postgres index")
    args = parser.parse_args()
    if args.fixture:
        from brain.runtime import fixture_runtime
        index = fixture_runtime().brain.index
    else:
        from brain.config import Settings
        from brain.retrieval import PostgresIndex
        index = PostgresIndex.connect(Settings.from_env().database_url)
    docs = index.documents_all()
    texts = {d.doc_id: " ".join(c.text for c in index.chunks_of(d.doc_id)) for d in docs}
    proposals = suggest(docs, texts, Registry.load())
    if not proposals:
        print("No proposals: every related pair found is already in one registered project.")
    for p in proposals:
        kinds = ", ".join(sorted({e["kind"] for e in p["evidence"]}))
        note = f"  (one side is already in {', '.join(p['already_in'])})" if p["already_in"] else ""
        print(f"{p['a']}  <->  {p['b']}   score {p['score']}: {kinds}{note}")
    print("\nNothing was changed. Add a pair to brain/projects/registry.json by hand, in a reviewed change, if it is right.")


if __name__ == "__main__":
    main()

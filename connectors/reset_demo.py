"""Bring the demo data back to the seeded Company A story, and say what only a person can fix.

    python -m connectors.reset_demo            # check only: what differs from the seed, and how to fix it
    python -m connectors.reset_demo --apply    # and reset what can be reset, re-ingest, and check the index

Checks, against the fixtures and the seed manifests:
- Simulators: each one answers; how many Leak-CI documents are planted. `--apply` resets both to the Company A seed
  (`POST /sim/admin/reset`), which also removes every planted document.
- Real Slack: every seeded thread exists with its seeded text and no later replies or edits, no extra threads sit in
  the seeded channels, and each persona can read exactly what the fixtures say (scenario 4's "before" state needs
  Priya in #auth-private). Slack cannot be changed from here (read-only token): the output says what to do by hand.
- Real Drive: each seeded file has its seeded text and readers. A version counter that moved (an edit, even one undone
  later) only needs the manifest rebuilt, which `--apply` does.
- `--apply` then ingests all four sources into DATABASE_URL (expired simulator cursors recrawl on their own) and runs
  `check_index`.
Exits 1 while anything differs. Prints fixture IDs, persona names and channel names only; no message text.
"""
import argparse
import os

import httpx

from connectors.env import load_dotenv
from fixtures.loader import load

SIMULATORS = {"confluence": ("CONFLUENCE_SIM_URL", "http://127.0.0.1:8101"), "jira": ("JIRA_SIM_URL", "http://127.0.0.1:8102")}


def _same_text(a: str, b: str) -> bool:
    return " ".join((a or "").split()) == " ".join((b or "").split())


def _expected(data: dict, persona: dict, doc: dict) -> bool:
    return bool(set(persona["tokens"]) & set(doc["acl"]["tokens"]))


def simulators(apply: bool, clients: dict[str, httpx.Client] | None = None) -> tuple[list[str], list[str]]:
    """`clients` (by simulator name) default to CONFLUENCE_SIM_URL and JIRA_SIM_URL."""
    from simulators.leakci import LeakCI

    lines, problems = [], []
    clients = clients or {name: httpx.Client(base_url=os.environ.get(env) or default, timeout=10)
                          for name, (env, default) in SIMULATORS.items()}
    try:
        planted = len(LeakCI(clients["confluence"], clients["jira"]).planted())
    except httpx.HTTPError as exc:
        return lines, [f"simulators: not reachable ({type(exc).__name__}); start them (make sim-confluence, make sim-jira)"]
    lines.append(f"simulators: answering; {planted} Leak-CI documents planted")
    for name, client in clients.items():
        if apply:
            client.post("/sim/admin/reset", json={"seed": "company_a"}).raise_for_status()
            lines.append(f"{name}: reset to the Company A seed")
        elif planted:
            problems.append(f"{name}: may hold planted or edited documents; --apply resets it")
    return lines, problems


def slack(data: dict) -> tuple[list[str], list[str]]:
    from connectors.slack.testing import RealSlack

    conn = RealSlack()
    docs = [d for d in data["documents"] if d["source"] == "slack"]
    problems: list[str] = []
    names: dict[str, str] = {}
    for doc in docs:
        try:
            got = conn.fetch(doc["doc_id"])
        except Exception as exc:   # noqa: BLE001  any failure here is something to report, not to crash on
            problems.append(f"slack: {doc['doc_id']} cannot be read ({type(exc).__name__}): re-seed it, then build_manifest")
            continue
        channel = doc["acl"]["native"]["channel"]
        names[channel] = got.title.split(" ", 1)[0]
        if not _same_text(got.body, doc["body"]):
            problems.append(f"slack: {doc['doc_id']} in {names[channel]} has other text than the seed (a reply or edit "
                            "left from a test?): delete it by hand")
        elif got.version != conn.seed_version(doc):
            problems.append(f"slack: {doc['doc_id']} in {names[channel]} was edited since the seed: check it by hand")
    seeded = {conn.manifest.real_doc(d["doc_id"]) for d in docs}
    seeded_channels = {conn.manifest.real_container(f"slack:{d['acl']['native']['channel']}") for d in docs}
    extra = [c.doc_id for c in conn.inner.list_changes(None).changes
             if c.doc_id not in seeded and f"slack:{c.doc_id.split(':', 1)[1].split('/', 1)[0]}" in seeded_channels]
    if extra:
        problems.append(f"slack: {len(extra)} extra thread(s) in the seeded channels (test messages?): delete them by hand")
    for persona in data["personas"]:
        identity = conn.resolve_identity(persona["email"])
        for doc in docs:
            want = _expected(data, persona, doc)
            got = identity is not None and conn.check_access(identity, doc["doc_id"]).allowed
            if want != got:
                channel = names.get(doc["acl"]["native"]["channel"], doc["acl"]["native"]["channel"])
                fix = (f"as outsider, /invite @{persona['display_name']} in {channel}" if want
                       else f"remove {persona['display_name']} from {channel}")
                problems.append(f"slack: {persona['id']} {'cannot' if want else 'can'} read {doc['doc_id']}: {fix}")
    return [f"slack: {len(docs)} seeded threads checked for {len(data['personas'])} personas"], sorted(set(problems))


def drive(data: dict, apply: bool) -> tuple[list[str], list[str]]:
    from connectors.gdrive.build_manifest import build
    from connectors.gdrive.connector import DriveConnector
    from connectors.gdrive.manifest import DEFAULT_PATH
    from connectors.gdrive.testing import RealDrive

    conn = RealDrive()
    docs = [d for d in data["documents"] if d["source"] == "gdrive"]
    lines, problems, moved = [], [], False
    for doc in docs:
        try:
            got = conn.fetch(doc["doc_id"])
        except Exception as exc:   # noqa: BLE001
            problems.append(f"gdrive: {doc['doc_id']} cannot be read ({type(exc).__name__})")
            continue
        if not _same_text(got.body, doc["body"]):
            problems.append(f"gdrive: {doc['doc_id']} has other text than the seed: restore it by hand (Version history)")
        readers = lambda tokens: sorted(p["id"] for p in data["personas"] if set(p["tokens"]) & set(tokens))   # noqa: E731
        if readers(got.acl.tokens) != readers(doc["acl"]["tokens"]):
            problems.append(f"gdrive: {doc['doc_id']} is shared with other people than the seed: fix its sharing by hand")
        moved |= got.version != conn.seed_version(doc)
    if moved and apply:
        manifest, missing = build(DriveConnector.from_env(), data)
        manifest.save(DEFAULT_PATH)
        lines.append("gdrive: version counters moved; manifest rebuilt")
        problems += [f"gdrive: {m}" for m in missing]
    elif moved:
        problems.append("gdrive: version counters moved since the manifest was built; --apply rebuilds it")
    lines.append(f"gdrive: {len(docs)} seeded files checked")
    return lines, problems


def reingest_and_check(data: dict) -> tuple[list[str], list[str]]:
    import psycopg

    from connectors.gdrive.manifest import SeedManifest as DriveManifest
    from connectors.ingestion.__main__ import SOURCES
    from connectors.ingestion.check_index import Ids, check
    from connectors.ingestion.embedding import from_env
    from connectors.ingestion.pg_store import DEFAULT_URL, PostgresStore
    from connectors.ingestion.pipeline import Ingestor
    from connectors.slack.manifest import SeedManifest as SlackManifest

    url = os.environ.get("DATABASE_URL") or DEFAULT_URL
    store = PostgresStore.connect(url)
    try:
        report = Ingestor([SOURCES[name]() for name in SOURCES], store, from_env()).run_once()
    finally:
        store.close()
    lines = [f"ingested: {', '.join(f'{s} {dict(a)}' for s, a in report.actions.items())}"]
    problems = [f"ingestion: {s} failed ({e})" for s, e in report.errors.items()]
    with psycopg.connect(url, connect_timeout=5) as conn:
        _, differ = check(conn, data, Ids(SlackManifest.load(), DriveManifest.load()), sources=list(SOURCES),
                          model=os.environ.get("EMBEDDING_BACKEND") or "bge-m3")
    lines.append("index: matches the fixtures" if not differ else f"index: {len(differ)} difference(s)")
    return lines, problems + [f"index: {d}" for d in differ]


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m connectors.reset_demo", description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="reset the simulators, rebuild the Drive manifest, re-ingest, check")
    args = parser.parse_args()
    load_dotenv()
    data = load()
    lines, problems = [], []
    for step in (lambda: simulators(args.apply), lambda: slack(data), lambda: drive(data, args.apply)):
        got_lines, got_problems = step()
        lines += got_lines
        problems += got_problems
    if args.apply:
        got_lines, got_problems = reingest_and_check(data)
        lines += got_lines
        problems += got_problems
    for line in lines:
        print(line)
    for problem in problems:
        print("TO FIX:", problem)
    if problems:
        raise SystemExit(1)
    print("The demo data matches the seeded story." if args.apply else "Nothing to fix. Run with --apply to reset and re-ingest.")


if __name__ == "__main__":
    main()

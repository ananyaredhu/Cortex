"""Check the real Drive set-up against the fixtures, read-only.

    python -m connectors.gdrive.check_setup

Reports who is signed in, which files the connector finds under the configured root folders, the tokens it gives
each, and, for every fixture Drive file, whether a file with that title exists and whether its tokens match.
Prints titles, canonical emails and tokens only: no tokens of the OAuth kind, no real addresses, no file text.
"""
from connectors.gdrive.connector import DriveConnector
from fixtures.loader import load


def report(connector: DriveConnector, data: dict) -> tuple[list[str], list[str]]:
    """(lines to print, problems)."""
    lines, problems = [], []
    found = {}
    for change in connector.list_changes(None).changes:
        doc = connector.fetch(change.doc_id)
        found[doc.title] = doc
        lines.append(f"found: {doc.title!r}  tokens={doc.acl.tokens}  author={doc.author}  chars={len(doc.body)}")
    for fixture in (d for d in data["documents"] if d["source"] == "gdrive"):
        doc = found.get(fixture["title"])
        if doc is None:
            problems.append(f"{fixture['doc_id']}: no readable file titled {fixture['title']!r} under the root folders")
            continue
        if doc.acl.tokens != fixture["acl"]["tokens"]:
            problems.append(f"{fixture['doc_id']}: tokens are {doc.acl.tokens}, the fixture has {fixture['acl']['tokens']}")
        if doc.body.strip() != fixture["body"].strip():
            problems.append(f"{fixture['doc_id']}: the text differs from the fixture")
    return lines, problems


def main() -> None:
    connector = DriveConnector.from_env()
    sessions, config = connector._sessions, connector._config   # noqa: SLF001  this tool is part of the package
    print(f"signed in ({len(sessions)}): {', '.join(sorted(sessions)) or 'nobody: run python -m connectors.gdrive.authorize <persona>'}")
    print(f"root folders: {len(config.root_folders)}   groups: {sorted(config.groups)}   org domains: {config.org_domains}")
    if not sessions or not config.root_folders:
        raise SystemExit("Nothing to check yet: sign at least one account in and fill in connectors/gdrive/gdrive.local.json.")
    lines, problems = report(connector, load())
    for line in lines or ["found: nothing under the root folders that a signed-in account can read the sharing of"]:
        print(line)
    for problem in problems:
        print("DIFFERS:", problem)
    if problems:
        raise SystemExit(1)
    print("Drive matches the fixtures.")


if __name__ == "__main__":
    main()

"""Build the seed manifest from the real Drive, by matching it against the fixtures.

A fixture file is matched by its exact title among the files the connector finds under the root folders, and its
fixture folder by the folder that file is in. Writes `connectors/gdrive/seed-manifest.local.json` (gitignored) and
reports whatever it could not match. Who can read each file is `check_setup`'s job; run both.

    python -m connectors.gdrive.build_manifest

Read-only on Drive. Prints fixture IDs and counts only: no real IDs, addresses, OAuth tokens or file text.
"""
from connectors.base import Connector
from connectors.env import load_dotenv
from connectors.gdrive.connector import DriveConnector
from connectors.gdrive.manifest import DEFAULT_PATH, SeedManifest
from fixtures.loader import load


def build(connector: Connector, data: dict) -> tuple[SeedManifest, list[str]]:
    """(manifest, problems). A problem is a fixture file or folder the Drive does not have exactly once."""
    by_title: dict[str, list] = {}
    cursor, more = None, True
    while more:
        batch = connector.list_changes(cursor)
        for change in batch.changes:
            if change.type in ("upsert", "acl_change"):
                doc = connector.fetch(change.doc_id)
                by_title.setdefault(doc.title, []).append(doc)
        cursor, more = batch.next_cursor, batch.has_more
    manifest, problems = SeedManifest(), []
    for fixture in (d for d in data["documents"] if d["source"] == "gdrive"):
        found = by_title.get(fixture["title"], [])
        if len(found) != 1:
            problems.append(f"{fixture['doc_id']}: found {len(found)} files titled {fixture['title']!r} under the root "
                            "folders, expected exactly 1")
            continue
        doc = found[0]
        manifest.files[fixture["doc_id"]] = doc.doc_id.split(":", 1)[1]
        manifest.versions[fixture["doc_id"]] = doc.version
        fixture_folder = fixture["parent_id"].split(":", 1)[1]
        real_folder = doc.parent_id.split(":", 1)[1] if doc.parent_id else None
        if real_folder is None:
            problems.append(f"{fixture['doc_id']}: no signed-in account can see the folder it is in")
        elif manifest.folders.setdefault(fixture_folder, real_folder) != real_folder:
            problems.append(f"{fixture['doc_id']}: in a different folder from the other files of {fixture['parent_id']}")
    return manifest, problems


def main() -> None:
    load_dotenv()
    manifest, problems = build(DriveConnector.from_env(), load())
    manifest.save(DEFAULT_PATH)
    print(f"wrote {DEFAULT_PATH.name}: {len(manifest.folders)} folders, {len(manifest.files)} files")
    for problem in problems:
        print("MISSING:", problem)
    if problems:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

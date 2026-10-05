"""Build the seed manifest from the real workspace, by matching it against the fixtures.

A fixture channel is matched by name (`#db-migration` -> `C_DBMIG`), a fixture thread by its exact text.
Writes `connectors/slack/seed-manifest.local.json` (gitignored) and reports whatever it could not find, so it
doubles as a check that the workspace was seeded as `docs/company-a-seed.md` says.

    python -m connectors.slack.build_manifest

Read-only on Slack. Prints channel names and fixture IDs only; never the token and never message text.
"""
from connectors.env import load_dotenv
from connectors.slack.client import SlackClient
from connectors.slack.connector import CHANNEL_TYPES
from connectors.slack.manifest import DEFAULT_PATH, SeedManifest
from fixtures.loader import load


def build(client: SlackClient, data: dict) -> tuple[SeedManifest, list[str]]:
    """(manifest, problems). A problem is a fixture channel or thread the workspace does not have (or the bot cannot read)."""
    wanted: dict[str, str] = {}   # channel name -> fixture channel id
    for doc in (d for d in data["documents"] if d["source"] == "slack"):
        wanted[doc["title"].split(" ")[0].lstrip("#")] = doc["acl"]["native"]["channel"]
    manifest, problems = SeedManifest(), []
    readable = {c["name"]: c for c in client.pages("conversations.list", "channels", types=CHANNEL_TYPES, limit=200)}
    for name, fixture_id in wanted.items():
        channel = readable.get(name)
        if channel is None:
            problems.append(f"channel #{name} ({fixture_id}) not found: create it, and invite the bot if it is private")
        elif not channel.get("is_member"):
            problems.append(f"channel #{name} ({fixture_id}): the bot is not a member, invite it")
        else:
            manifest.channels[fixture_id] = channel["id"]
    for doc in (d for d in data["documents"] if d["source"] == "slack"):
        channel_id = manifest.channels.get(doc["acl"]["native"]["channel"])
        if channel_id is None:
            continue
        roots = [m for m in client.pages("conversations.history", "messages", channel=channel_id, limit=200)
                 if m.get("thread_ts", m.get("ts")) == m.get("ts") and (m.get("text") or "").strip() == doc["body"].strip()]
        if len(roots) == 1:
            manifest.threads[doc["doc_id"]] = roots[0]["ts"]
        else:
            problems.append(f"{doc['doc_id']}: found {len(roots)} messages with the fixture text, expected exactly 1")
    return manifest, problems


def main() -> None:
    load_dotenv()
    manifest, problems = build(SlackClient.from_env(), load())
    manifest.save(DEFAULT_PATH)
    print(f"wrote {DEFAULT_PATH.name}: {len(manifest.channels)} channels, {len(manifest.threads)} threads")
    for problem in problems:
        print("MISSING:", problem)
    if problems:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

# Slack connector

A real Slack workspace through a read-only bot token. Implements `docs/02-contracts/connector-interface.md`.

## What it needs
1. **A custom internal app** in the workspace with these bot token scopes, all read-only: `channels:read`,
   `channels:history`, `groups:read`, `groups:history`, `users:read`, `users:read.email`.
2. **`SLACK_BOT_TOKEN`** in `.env` (the `xoxb-` token). Never paste it into a chat, a prompt, an issue or a screenshot.
3. **The bot invited to every channel it should read, public ones too** (`/invite @<app name>`). A bot can only read
   the history of channels it is a member of. A channel without the bot does not exist for this connector.
4. **The identity map**, `connectors/identity-map.local.json`: canonical email to the address of the Slack account
   playing that persona. An account that is not in it has no access and is nobody's author.

## First steps with a new token
```
python -m connectors.slack.check_rate_limit     # check #3: is the app exempt from the 1-request-per-minute cut?
python -m connectors.slack.build_manifest       # match the workspace to the fixtures; lists anything missing
python -m connectors.ingestion --once --sources confluence,jira,slack
```
All three read `.env` themselves. None prints the token or any message text.

## How it maps Slack to the contract
| Contract | Slack |
|---|---|
| Document | One thread: the root message plus its replies. A lone message is a thread of one |
| `doc_id` | `slack:<channel id>/<thread ts>`, with the workspace's own IDs |
| `version` | Timestamp of the latest reply or edit |
| Tokens | Public channel: `channel:<id>` and `public:org`. Private channel: `channel:<id>` |
| `native` | `{"channel": <id>, "private": <bool>}` |
| `resolve_identity` | Identity map, then `users.lookupByEmail`. Tokens: one `channel:<id>` per channel the person is in, plus `public:org` unless they are a guest |
| `check_access` | Live: the thread exists, the account is active and still maps to that person, and they are a member of the channel or a full member reading a public channel. Any error, timeout or rate limit is a deny |

Guests (`is_restricted`, `is_ultra_restricted`) never hold `public:org`, so they read only the channels they are in.
On a free workspace there are no guest accounts: everyone invited is a full member and can read every public
channel.

## Changes
`list_changes` polls. Every call scans the workspace (users, channels, members, channel history) and compares it
with the previous scan, which is kept in memory.

| What happened in Slack | Change emitted |
|---|---|
| New thread, new reply, root message edited, reply deleted | `upsert` |
| Thread deleted, or the bot removed from the channel | `delete` |
| Channel made private or public | `acl_change` for each of its threads |
| Someone joins or leaves a channel, or changes between guest and full member | `principal_change` with the token |

Limits, all known:
- **Edits to a reply are not detected** by polling. The Events API would see them; it is not built.
- **After a restart** the previous scan is gone. The next call returns every thread as an `upsert` (ingestion skips
  what is unchanged). Deletes and membership changes that happened while it was down are not reported; run
  ingestion with `--recrawl` to clean up. `check_access` is live, so access itself is never stale.
- **Cost of a scan:** two calls plus two per channel, more with paging. Fine for a handful of channels. It will not
  do for 200 channels without the Events API.
- **Not covered:** DMs and group DMs, files, links to other documents (`links` is always empty), archived-channel
  special cases.
- `check_access` asks Slack four questions at once (channel, thread, user, members) and caches nothing. Measured on
  the real workspace on 5 Oct: median about 270 ms, worst about 310 ms. One after another they took about 740 ms.
  All of them together must finish within 3 seconds or the answer is deny.

## IDs and the seed manifest
The fixtures say `slack:C_DBMIG/thread-1`; the workspace says `slack:C07ABC123/1791619200.000100`. The connector
always uses the real ones. `seed-manifest.local.json` (gitignored, built by `build_manifest`) maps between them, and
`testing.ManifestSlack` wraps the connector so the shared contract tests can speak fixture IDs.

## Tests
`fake.py` is an in-memory Slack Web API with the same shapes, paging, errors and bot-membership rules. The
connector runs over it through real HTTP code. `SeededSlack` in `testing.py` is registered in the shared contract
tests as `slack-fake`. Two contract tests are expected failures for Slack: restricting a single document and
revoking a container grant have no Slack equivalent. `tests/` covers the Slack-specific behaviour instead.

No test calls the real Slack API yet.

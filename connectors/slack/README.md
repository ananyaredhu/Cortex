# Slack connector

A real Slack workspace through a read-only bot token. Implements `docs/02-contracts/connector-interface.md`.

## What it needs
1. **A custom internal app** in the workspace with these bot token scopes, all read-only: `channels:read`,
   `channels:history`, `groups:read`, `groups:history`, `users:read`, `users:read.email`.
2. **`SLACK_BOT_TOKEN`** in `.env` (the `xoxb-` token). Never paste it into a chat, a prompt, an issue or a screenshot.
   For events, also **`SLACK_APP_TOKEN`** (the `xapp-` app-level token, scope `connections:write`), with Socket Mode
   on and the bot events listed under "Events" below subscribed.
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
channel. That is why Sam, the contractor persona, has no account on our workspace and no Slack access (decided
5 Oct). The guest rules are still tested, in `tests/`, with a guest who is not a persona.

## Changes
`list_changes` scans: every call reads the workspace (users, channels, members, channel history) and compares it
with the previous scan. The previous scan is kept in memory and also saved in the cursor (thread IDs, which channels
are private, and which mapped people are in each channel, as canonical emails), so a new process, such as ingestion
run with `--once`, still sees what changed since the last run.

| What happened in Slack | Change emitted |
|---|---|
| New thread, new reply, root message edited, reply deleted | `upsert` |
| Reply edited | `upsert`, only through an event (a scan cannot see it) |
| Thread deleted, or the bot removed from the channel | `delete` |
| Channel made private or public | `acl_change` for each of its threads |
| Someone joins or leaves a channel, or changes between guest and full member | `principal_change` with the token |

## Events (Socket Mode)
```
python -m connectors.ingestion --poll 600 --sources confluence,jira,slack,gdrive --slack-events
```
`events.py` keeps a Socket Mode websocket open from inside the ingestion process: outbound only, so no public URL
and no tunnel, and still one writer. It acknowledges every envelope at once and keeps only IDs from an event; it
never logs message text, the token or the websocket URL. An event is a hint, not data:

| Event | What it does |
|---|---|
| `message` in a channel the bot reads (new, reply, edit, delete, `message_replied`) | `note_thread`: that thread is re-sent as an `upsert`. A deleted thread becomes `DocumentNotFound` on fetch, so ingestion deletes it |
| `member_joined_channel`, `member_left_channel`, `channel_*`, `group_*`, `user_change`, `team_join`, join and leave messages | `note_structure`: the next pass scans the whole workspace and diffs, as above |
| Anything else, DMs included | Nothing |

Two seconds after an event (so a burst becomes one pass), ingestion runs Slack alone. If only thread events
arrived, it fetches just those threads (two calls each) without scanning: this is what lets events scale to
200 channels. A structural event, a reconnect (events may have been missed) or a fresh process with nothing in
memory makes that pass a full scan instead. The scheduled `--poll` pass always scans, as the fallback for a lost
event. The change is dated from when the event arrived, so `pipeline_lag_seconds` counts from the event;
`freshness_lag_seconds` counts from the edit's own time in Slack (`connectors/ingestion/README.md`, "Freshness").

Bot events to subscribe to (Event Subscriptions, all covered by the read scopes above): `message.channels`,
`message.groups`, `member_joined_channel`, `member_left_channel`, `channel_created`, `channel_deleted`,
`channel_rename`, `channel_archive`, `channel_unarchive`, `group_rename`, `group_archive`, `group_unarchive`,
`group_left`, `user_change`, `team_join`.

If the log says `Slack events: connected` but edits never wake ingestion, the subscriptions are not active: check
that Enable Events is on, that the bot events are listed, that Save Changes at the bottom of the page was clicked,
and reinstall the app if Slack shows a banner asking for it (6 Oct: events started only after this).

Measured on the real workspace, 6 Oct: a reply, an edit to that reply and its deletion each reached the index
4 to 5 seconds after the event arrived, 2 of them the settle wait. A Socket Mode reconnect in the middle of the test
lost nothing. Without events the edit would have waited for the next poll and then been missed.

Limits, all known:
- **Without events, edits to a reply are not detected.** A scan sees only the root's edit and reply count.
- **After a restart** every thread is re-sent as an `upsert` (ingestion skips what is unchanged), because the saved
  scan has thread IDs but not their versions. Deletes and membership changes since the saved cursor are reported.
  A cursor from before 6 Oct, or an unreadable one, falls back to a plain crawl without them. `check_access` is
  live, so access itself is never stale.
- **Cost of a scan:** two calls plus two per channel, more with paging. Events avoid it for content changes; the
  scheduled poll still pays it, so with 200 channels keep `--poll` long (10 minutes or more).
- **A channel made private or public** sends no event of its own that we rely on; the scheduled scan catches it.
- **Not covered:** DMs and group DMs (would need `im:history` and `mpim:history`, wider scopes), files, links to
  other documents (`links` is always empty), archived-channel special cases.
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

`tests/test_events.py` runs the listener against a local websocket server speaking Socket Mode (hello, envelopes,
acks, disconnect and reconnect) and checks what the connector does with each kind of hint.

`RealSlack` registers the real workspace in the shared contract tests as `slack-real`, opt-in because it needs
`.env` and the seed manifest. The workspace must be in the "before" state (Priya in `#auth-private`):
```
CONTRACT_REAL=slack python -m pytest connectors/tests/contract -k slack-real
CONTRACT_REAL=slack CONTRACT_REAL_HAND=1 python -m pytest connectors/tests/contract -k slack-real -s
```
The bot token is read-only, so the revocation test (scripted event `e2`) needs a person: with `CONTRACT_REAL_HAND=1`
it prints what to do (remove Priya from `#auth-private`, e.g. as `outsider`) and waits until the live `check_access`
shows it; without it the test is an expected failure. Re-invite Priya afterwards. Channels the manifest does not map
(`#general` and the like) are left out of identities seen through it: they hold no seeded documents.
Run 6 Oct with the hand step: 11 passed, 1 skipped (no scripted Slack edit), 2 expected failures (no per-thread
restriction or channel grant in Slack). The revocation flipped Priya to deny and the feed emitted exactly one
`principal_change`. The default test run never calls the real Slack API.

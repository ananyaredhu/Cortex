# Google Drive connector

Google Drive on personal accounts, through each persona's own read-only OAuth grant. Implements
`docs/02-contracts/connector-interface.md`.

There is no paid Workspace behind this: no organisation, no domain-wide delegation, no readable group membership.
The connector asks Drive for everything Drive can tell it and takes the rest from a local config file.

## Set-up, once
1. **Google Cloud project** with the Drive API enabled.
2. **OAuth consent screen** in Testing mode, with every persona account added as a test user.
3. **OAuth client of type "Desktop app".** Put its ID and secret in `.env` as `GOOGLE_OAUTH_CLIENT_ID` and
   `GOOGLE_OAUTH_CLIENT_SECRET`. Never paste them anywhere else.
4. **Identity map:** add a `gdrive` address for each persona in `connectors/identity-map.local.json`.
5. **Config:** copy `gdrive.example.json` to `gdrive.local.json` and fill in the folder IDs (the long ID at the end
   of a folder's URL), the group members and the organisation's domain.
6. **Sign each persona in:**
   ```
   python -m connectors.gdrive.authorize priya@companya.com
   ```
   A browser opens; sign in as the account that plays that persona. The refresh token goes to
   `token-gdrive-<persona>.json` here, which is gitignored. The tool refuses to save if a different account signed in.
7. **Check it:**
   ```
   python -m connectors.gdrive.check_setup
   python -m connectors.ingestion --once --sources confluence,jira,slack,gdrive
   ```

## The two rules that make this safe
**Only the root folders exist.** The persona accounts are people's own Google accounts. The connector reads nothing
outside the folders listed in `root_folders`, and their subfolders. A file elsewhere cannot be listed, fetched or
checked, even by its ID. No roots configured means nothing is read.

**No sharing list, no document.** Drive shows a file's full sharing list only to people who can share it: the owner
or an editor. If none of the signed-in accounts can read a file's sharing, the file is not indexed and every access
check on it is a deny. **So the owner or an editor of every seeded file must be one of the signed-in accounts.**

## How it maps Drive to the contract
| Contract | Drive |
|---|---|
| Document | One file. Folders are containers, not documents |
| `doc_id`, `parent_id` | `gdrive:<file id>`, `gdrive:<folder id>` |
| `version` | The file's modified time. Sharing a file does not change it |
| Body | Google Docs as plain text; `text/*` files as they are. Anything else has no body and is indexed by its title |
| `native` | Folder, and the sharing list with canonical emails and group names only. Real addresses are not stored |
| `resolve_identity` | From the identity map and the config: the person's groups, plus `external:` if outside the organisation. Not a live lookup |
| `check_access` | Live: reads the file and its sharing list from Drive now, and decides from that list |

Tokens, from the sharing list (which already includes what the file inherits from its folders):

| In the sharing list | Token |
|---|---|
| A mapped person inside `org_domains` | `user:<canonical email>` |
| A mapped person outside | `external:<canonical email>` |
| A Google Group whose address is in the config | `group:gdrive:<name>` |
| Every member of a configured group, shared one by one | `group:gdrive:<name>`, in place of their own tokens |
| An account not in the identity map, "anyone with the link", an unknown group, a whole domain | none |

The owner is in the sharing list like anyone else, so a mapped owner gets a token. No Drive file ever carries
`public:org`.

## Changes
`list_changes` polls: it lists the root folders with every signed-in account, reads each file's sharing, and
compares with the previous scan, kept in memory.

| What happened in Drive | Change emitted |
|---|---|
| New file, or content edited | `upsert` |
| Shared, unshared, role changed, or a parent folder's sharing changed | `acl_change` |
| Trashed, moved out of the root folders, or no longer readable by any signed-in account | `delete` |

Group membership lives in the config, so the connector emits no `principal_change`.

## Limits, all known
- **Group membership is not live.** It is whatever `gdrive.local.json` says. A person removed from a real Google
  Group keeps passing the check until the config is edited. Files shared one by one do not have this problem.
- **A person in a configured group that the file is shared with by address is allowed on the config's word.**
- **"Anyone with the link" is ignored**, so such a file is only served to people it is also shared with by name.
- **Polling cost:** one listing per folder per signed-in account, plus one sharing read per file. Fine for a few
  folders. Not built: `changes.watch` push notifications (check #4) and `changes.list`.
- **After a restart** every file is re-sent as an `upsert`; run ingestion with `--recrawl` to clean up deletes missed
  while it was down.
- **Not covered:** shared drives, shortcuts, files with several parents, Sheets and Slides content, `links`.
- **No seed manifest yet.** The fake uses the fixture IDs. Real Drive assigns its own, and the contract tests and
  golden cases will need a mapping, as Slack has.
- `check_access` against the real API takes about 0.5 s (5 Oct, three signed-in accounts, two files).

## Tests
`fake.py` is an in-memory Drive v3 and token endpoint: per-account visibility, inherited sharing, sharing lists only
for owners and editors. `SeededDrive` in `testing.py` is registered in the shared contract tests as `gdrive-fake` and
passes all of them, including restriction and container revocation. `tests/` covers the Drive-specific behaviour.

No test calls the real Google API. The sign-in flow and `check_setup` have been run by hand against real Drive (5 Oct).

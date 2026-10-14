# Connector interface

version: 0.6
**0.6 (14 Oct; `can_create` and `audience` built in the fixture connector, `create` comes with step 3):** connectors can create items: `can_create`, `audience`, `create` and `delete_created`, for every connector. See "Writing: creating items" below and [ADR-010](../05-decisions/ADR-010-actions.md).
**0.5 (12 Oct):** the Jira `due`, `priority` and `assignee_email` fields, and `WritableConnector.transition`, the first write a connector makes. See "Writing: moving a Jira issue" below.
Producer: the connectors. Consumers: the Brain (ingestion, PDP, JIT checks).

Every source (Slack, Google Drive, Confluence, Jira), real or simulated, implements the same protocol and passes the same **contract tests**. Simulators must be proven faithful by running the same tests against the real API wherever one exists.

## Protocol (Python)
```python
from typing import Protocol, Literal, Iterable

Source = Literal["slack", "gdrive", "confluence", "jira"]

class Connector(Protocol):
    source: Source

    def resolve_identity(self, email: str) -> "PlatformIdentity | None":
        """Map the canonical (IdP) email to this platform's identity (Slack user ID, Jira accountId...).
        The platform account may use a different address: see "Identity mapping" in acl-model.md.
        Returns None when there is no mapping (fail closed: no access on this platform)."""

    def list_changes(self, cursor: str | None) -> "ChangeBatch":
        """cursor=None starts a full crawl: every document that exists now is returned as an `upsert`,
        paged with `has_more` / `next_cursor`. This is the only way to enumerate a source.
        A non-null cursor returns incremental changes since that cursor
        (created/updated/deleted docs AND permission changes)."""

    def fetch(self, doc_id: str) -> "Document":
        """Current content, links and ACL evidence for one document.
        Raises DocumentNotFound if the document does not exist or was deleted."""

    def check_access(self, identity: "PlatformIdentity", doc_id: str) -> "AccessDecision":
        """Authoritative, live answer: may this identity read this doc right now?
        Called just-in-time by the PDP. Must be fast (<300 ms typical) and cacheable by the caller (short TTL)."""

    def version(self, doc_id: str) -> str:
        """Cheap source version (etag / updated timestamp) used to detect staleness.
        Raises DocumentNotFound like `fetch`."""


class DocumentNotFound(LookupError):
    """Raised by `fetch` and `version`. Never raised by `check_access`, which returns a deny instead."""


# (0.5) Implemented by the Jira connector only. Every other connector stays read-only.
class WritableConnector(Connector, Protocol):
    def transition(self, identity: "PlatformIdentity", doc_id: str, status: str) -> "Document":
        """Move one issue to `status`, acting as `identity`, and return the issue as the platform now holds it
        (what `fetch` would return, with the new `version`).
        `status` is matched without regard to case against the transitions the platform offers for this issue.
        Raises DocumentNotFound if the issue does not exist; TransitionRefused if the platform will not make the
        move (no such transition from the current status, or the platform denies this identity); any other error
        or a timeout means nothing is known to have changed, and the caller must not assume either way."""


class TransitionRefused(Exception):
    """Raised by `transition`. Carries no reason that names anything beyond the issue itself."""


# (0.6, proposed) Creating items. Implemented by every connector; `transition` stays Jira only.
# A destination is a container id: "jira:<project key>", "slack:<channel id>", "slack:<channel id>/<thread ts>"
# (a thread reply), "confluence:<space>", "gdrive:<folder id>". For `jira.link_issues` and `jira.add_comment`
# it is the issue the operation attaches to.
class CreatingConnector(Connector, Protocol):
    def can_create(self, identity: "PlatformIdentity", destination: str) -> bool:
        """Live: may this identity create in `destination` right now? (check P3)
        Jira: Create Issues in the project. Slack: a member of the channel, and the channel is not read-only
        to them. Confluence: add-page in the space. Drive: writer on the folder.
        False for a destination that does not exist or that the identity cannot see, and on any error or
        timeout (fail closed). Never raises DocumentNotFound: like check_access, it answers with a deny."""

    def audience(self, destination: str) -> "Audience":
        """Who can read what is created in `destination` (checks P4 and P5). An item created there takes the
        container's ACL, so this is the container's readers. Raises DocumentNotFound for an unknown destination.
        Called by the policy plane only; an Audience never leaves it."""

    def create(self, identity: "PlatformIdentity", op: "CreateOp", idempotency_key: str) -> "Document":
        """Create one item as `identity` and return it as the platform now holds it (what `fetch` returns).
        A second call with the same idempotency_key returns the item the first call created and creates nothing.
        Raises WriteRefused if the platform will not create it for this identity; DocumentNotFound if the
        destination does not exist; any other error or a timeout means nothing is known to have been created,
        and the caller must not assume either way. Never retries on its own."""

    def delete_created(self, identity: "PlatformIdentity", doc_id: str) -> None:
        """(P1, Undo) Delete an item this Brain created, as `identity`. Refuses (WriteRefused) any doc_id the
        Brain did not create through `create`. A Drive doc goes to the trash, not deleted for good."""


class WriteRefused(Exception):
    """Raised by `create` and `delete_created`. Carries no reason that names anything beyond the destination."""
```

## Data types
```python
class PlatformIdentity:
    source: Source
    platform_user_id: str
    email: str                 # canonical (IdP) email, not the platform account's address
    groups: list[str]          # ACL tokens this identity holds on this platform, in acl-model.md format
                               # (channel:, group:<source>:, role:, external:, public:org).
                               # The user: token is not included; the PDP adds it.

class Document:
    doc_id: str                # globally unique: "<source>:<native id>"
    source: Source
    kind: str                  # page | issue | message | thread | file | comment
    title: str
    url: str
    body: str                  # text content (markdown ok)
    parent_id: str | None      # space/project/channel/folder container
    links: list[str]           # doc_ids this doc references (cross-platform links allowed)
    author: str | None         # canonical email when the platform account is mapped, else None
    created_at: str
    updated_at: str
    version: str
    acl: "AclEvidence"
    fields: dict[str, str]     # (0.4) what the platform holds as a field, not as text: lower-case name -> value, both strings.
                               # {} when it has none. Jira: "status", "assignee" (display name). Untrusted source data, like body.
                               # (0.5) Jira also: "due" (YYYY-MM-DD), "priority" (the platform's name) and
                               # "assignee_email" (canonical email, only when the assignee's account is mapped).

class Audience:                # (0.6) who can read what is created at a destination
    principals: list[str] | None   # canonical emails of every reader; None when not enumerable
                                   # (anyone with the link, anonymous access, readers with no canonical mapping)
    has_external: bool             # some reader is a guest or external person (external:, Slack guests)
    snapshot_hash: str             # sha256 of canonical(native membership); changes when the audience does

class CreateOp:                # (0.6) one operation of a confirmed plan, already decided by the policy plane
    type: str                  # an action type from skills-format.md 0.4, e.g. "jira.create_issue"
    destination: str           # as above
    fields: dict[str, str]     # the exact previewed fields, e.g. summary, description, issuetype, parent,
                               # priority, labels (comma-separated), text (Slack), title and body (page, doc)
    assignee_account: str | None   # resolved by the policy plane from a display name; never from the model

class AclEvidence:
    tokens: list[str]          # effective allow tokens at observed_at (see acl-model.md)
    native: dict               # source-native raw ACL data kept for audit and replay
    snapshot_hash: str         # sha256 of canonical(native)
    observed_at: str

class ChangeBatch:
    changes: list["Change"]
    next_cursor: str
    has_more: bool

class Change:
    type: Literal["upsert", "delete", "acl_change", "principal_change"]
    doc_id: str | None         # set for upsert, delete, acl_change; None for principal_change
    principal: str | None      # principal_change only: "user:<canonical email>" whose token set changed
    token: str | None          # principal_change only: the token gained or lost, e.g. "channel:C123"
    detected_at: str

class AccessDecision:
    allowed: bool
    proof_path: list[str]      # minimal grant path, e.g. ["user:priya@companya.com", "group:confluence:payments-eng"]
    evaluated_at: str
    acl_snapshot_hash: str
    policy_version: str
```

## Chunk (indexed unit, written by A's ingestion, read by B's retrieval)
```python
class Chunk:
    chunk_id: str              # "<doc_id>#<position>"
    doc_id: str
    source: Source             # derived from doc_id by the database
    position: int
    text: str
    acl_tokens: list[str]      # copied from the document's AclEvidence
    acl_snapshot_hash: str
    source_version: str        # Document.version at index time
    embedding: list[float] | None   # 1024 dimensions; None when indexed without a model (EMBEDDING_BACKEND=none)
    embedding_model: str       # e.g. "bge-m3"
    embedding_version: str
    ingested_at: str
```
Vectors carry the **same ACL tokens as their chunk** and are never shared across ACLs.

On a `delete`, ingestion **removes the document's chunks** (the `documents` row may stay as a tombstone with `deleted = true`). A deleted document must not remain searchable.

## Reading the index (B's retrieval)
The tables are in `db/init.sql`: `documents`, `chunks`, `acl_snapshots`. A writes them (ingestion, the only writer); B reads them. Ingestion's outbox of permission events, `ingestion_events`, is described in `connectors/ingestion/README.md`. `connectors/ingestion/tests/test_index_reads.py` runs the queries below against what ingestion writes.

What B can rely on:
- `chunks.acl_tokens` and `acl_snapshot_hash` are the document's current ACL; every chunk of a document has the same ones. A document nobody may read has `acl_tokens = '{}'`.
- A deleted document has no chunks, so retrieval does not need to check `documents.deleted`.
- `source_version` is `Document.version` at index time: compare it with the connector's `version(doc_id)` for freshness read-through.
- `documents.fields` (jsonb, 0.4) is `Document.fields` at index time, `{}` for a document indexed before 0.4 until it is indexed again (`python -m connectors.ingestion --once --recrawl --sources jira`).
- For a context-packet item: `title` and `url` come from `documents`; `as_of` is `documents.updated_at`, or `chunks.ingested_at` when that is null.

Rules for every read:
1. **Prefilter on every query:** `acl_tokens && :asker_tokens`. No query reads `chunks` without it, and the JIT `check_access` still runs on what it returns.
2. **Vectors:** only rows with `embedding IS NOT NULL` and `embedding_model` equal to the model that embedded the query.
3. **Iterative index scan:** run vector queries with `SET LOCAL hnsw.iterative_scan = relaxed_order` (pgvector 0.8 or later), inside a transaction. Without it the HNSW index returns its nearest candidates first and the ACL filter can discard all of them: in the test, a persona with a narrow ACL got 0 of 5 results. `relaxed_order` can return rows slightly out of order, so re-sort.
4. **Per-source fan-out** filters on `chunks.source`.

Reference queries:
```sql
-- vector leg (in a transaction, after SET LOCAL hnsw.iterative_scan = relaxed_order)
WITH relaxed AS MATERIALIZED (
    SELECT chunk_id, doc_id, embedding <=> :query_vector AS distance
    FROM chunks
    WHERE acl_tokens && :asker_tokens AND embedding_model = :model AND embedding IS NOT NULL
      AND source = :source                      -- per-source fan-out
    ORDER BY distance
    LIMIT :k
)
SELECT chunk_id, doc_id, distance FROM relaxed ORDER BY distance;

-- keyword leg (Postgres full-text search, English configuration, over chunks.text)
SELECT chunk_id, doc_id, ts_rank_cd(tsv, query) AS rank
FROM chunks, websearch_to_tsquery('english', :question) AS query
WHERE tsv @@ query AND acl_tokens && :asker_tokens AND source = :source
ORDER BY rank DESC
LIMIT :k;
```

## Document granularity and IDs
| Source | One `Document` is | `doc_id` | `parent_id` | `version` |
|---|---|---|---|---|
| Slack | One thread: the root message plus its replies. A message with no replies is a thread of one | `slack:<channel id>/<thread ts>` | `slack:<channel id>` | Timestamp of the latest reply or edit |
| Slack DM or group DM | The same: one thread of the conversation. `kind` stays `thread` | `slack:<conversation id>/<thread ts>` (the ID starts with `D` for a DM; a group DM has a `G` or `C` ID like a channel) | `slack:<conversation id>` | The same |
| Google Drive | One file | `gdrive:<file id>` | `gdrive:<folder id>` | File version or modified time |
| Confluence | One page | `confluence:<space>/<page id>` | `confluence:<space>` | Page version |
| Jira | One issue, including its comments. After the description the `body` carries one line `Status: <name>` and one line `Assignee: <display name>` when the issue has them (Team focus reads these lines). The same two values are in `fields` as `status` and `assignee` (0.4): the lines are for a reader and for search, the fields are for code that must compare a value exactly | `jira:<issue key>` | `jira:<project key>` | Updated timestamp |

*(0.5)* A Jira issue's `body` also carries `Due: <YYYY-MM-DD>` and `Priority: <name>` lines when the issue has them, after `Assignee:`, and `fields` carries `due`, `priority` and `assignee_email`. The lines let a question such as "what is due this week?" be answered and checked; the fields are what `/v1/team` and `/v1/mywork` read. `assignee_email` is a field only, never a line in `body`: it exists so that code can tell whose an issue is by account rather than by display name, and it is absent when the assignee's account has no canonical mapping (then nobody is treated as the assignee).

Real platforms assign their own IDs (Slack channel IDs, Drive file IDs), so they will not equal the IDs in `fixtures/`. A real backend ships a **seed manifest** that maps each fixture `doc_id` and container to its native ID; the contract tests translate through it.

## Behavior requirements
- `list_changes(None)` enumerates the whole source as `upsert`s (initial load). Ingestion calls it once, then keeps the returned cursor.
- A source that no longer recognizes a cursor (its change log began again, e.g. a simulator restart) raises `CursorExpired` from `list_changes`. The consumer then crawls again with `list_changes(None)`. Never answer an expired cursor with an empty or partial feed: changes would be lost silently.
- Permission changes come in two kinds (see "Two kinds of permission change" in acl-model.md):
  - **`acl_change`**: the document's own ACL changed (page restriction added, file unshared, channel made private). The document's tokens change, so ingestion re-fetches it and rewrites its chunks' `acl_tokens`.
  - **`principal_change`**: a person's membership changed (removed from a channel, group or role). No document's tokens change, so there is **one** change entry, not one per document. The consumer drops cached identities and cached decisions for that principal.
- Deleted or no-longer-visible docs must be reported so they can be dropped from the index.
- `check_access` must never return `allowed=True` on an error or timeout: **fail closed**.
- A deny for a forbidden document and for a nonexistent document has `allowed=False` and `proof_path=[]`. `acl_snapshot_hash` is empty when the document does not exist and may be set when it does (the audit log needs it for replay), so an `AccessDecision` **never leaves the policy plane**: it is not serialized to the LLM plane or to the user.
- Rate limits: connectors back off and surface lag as a metric (`freshness_lag_seconds`).
- Credentials are read-only, least-privilege, and live only in the connector process. *(0.5)* One exception: the Jira connector's credential may also transition issues, and nothing else (see "Writing" below). *(0.6)* And every connector may create items as the person, with the person's own write grant where the platform has one (see "Writing: creating items"). A connector that reads with per-person credentials (Drive's refresh tokens, Slack's user tokens for DMs) answers `check_access` for a person-private document with **that person's own** credential, and denies when it has none.

## Writing: moving a Jira issue *(0.5)*
`transition` exists for one caller: the Brain's `POST /v1/issues/move` ([api](api.md) 0.3), after the policy plane has decided that the person may open the issue and is its assignee. It is the only write in this interface.
- **The platform stays the source of truth.** The connector changes the status in Jira and returns what Jira then holds. It keeps no status of its own and never edits the index; the Brain passes the returned `Document` to ingestion's upsert path, the same one a change event takes.
- **The change still appears in the feed.** After a transition, `list_changes` emits an `upsert` for the issue as for any edit. Ingestion meets a version it already has and does nothing more, so the next sync cannot undo or contradict the move.
- **Acting as the person.** Where the platform lets the connector act with the person's own credential or on their behalf, it does, so Jira's own permissions and workflow apply to them. Where it cannot (a single service credential), the connector makes the move with that credential and Jira's workflow still applies; the Brain's assignee rule is then the only per-person check, which is why it compares accounts (`assignee_email`), not display names. The simulator checks both: the acting account must be allowed to transition issues in the project, and the workflow must offer the move.
- **Fail closed, and never guess.** A missing identity is a refusal. An error or a timeout is reported as such; the connector does not retry a write on its own.
- **Scope.** The status of one issue per call. No other field, no comment, no bulk call. The write credential, like the read one, lives only in the connector process and never reaches the LLM plane.
- The simulator serves `GET` and `POST /rest/api/2/issue/{key}/transitions` in real Jira's shapes, acting as the `X-Sim-User` header's account, over one workflow shared with the fixture connector (`connectors/workflow.py`): To Do → In Progress → In Review or Done, In Review → In Progress or Done, Done → In Progress, and In Progress → To Do. To Do cannot go straight to Done, which is the refusal the tests and the demo use.
- `assignee_email` never reaches the LLM plane: the context packet leaves it out (`brain/packet.py`, `POLICY_ONLY_FIELDS`). The model gets the assignee's display name, as the issue's text already says it.

## Writing: creating items *(0.6, 14 Oct; not built yet)*
`create` exists for one caller: the Brain's `POST /v1/actions/{plan_id}/confirm` ([api](api.md) 0.6), after the policy plane has allowed the operation (checks P1 to P6 in [acl-model](acl-model.md) "Writes") and a person has confirmed the exact payload. The rules of `transition` apply unchanged:
- **The platform stays the source of truth.** The connector creates the item and returns what the platform then holds. It keeps no copy and never edits the index; the Brain passes the `Document` to ingestion's upsert path. `list_changes` still emits an `upsert` for it, and ingestion meets a version it already has.
- **Acting as the person.** The Jira and Confluence simulators act as the `X-Sim-User` account and enforce the project or space permission. Real Slack posts with the person's own user token at `chat:write` (added to the scope list in `connectors/slack/user_tokens.py`; existing grants re-authorize). Real Drive creates with the person's own OAuth grant at `drive.file`, which can create files but cannot read other files. **A person with no write grant is refused** (`WriteRefused`), and the UI asks them to connect; the bot never posts on a person's behalf (ADR-010, Q2).
- **A write never changes anyone's access.** `create` never sets sharing, restrictions or members: a created item takes its container's ACL. The contract test checks that no other document's `check_access` result changes.
- **Fail closed, never guess, no retry.** A missing identity is a refusal. An error or a timeout is reported as such. Idempotency keys make a repeated confirm safe.
- **Audience is exact or absent.** `audience` lists every reader by canonical email, expanded through the identity map: Jira project roles and groups; a channel's members (for a public Slack channel, every full member of the workspace plus the channel's guests); a space's groups and permissions; a folder's sharing and inheritance. A reader with no canonical mapping, link sharing or anonymous access makes `principals` `None`; any guest or external reader sets `has_external`. Never a partial list.
- **Scope of the write credential.** Create and, for Undo, delete what the Brain created. No edit of existing content, no permission change, no other call. It lives only in the connector process and never reaches the LLM plane, logs or audit payloads.
- **Simulator endpoints** in the real platforms' shapes, so a real backend can replace them. Each acts as `X-Sim-User`, enforces the permission and emits a change in `/sim/changes`. Jira: `POST /rest/api/2/issue` (`issuetype`, `parent` or epic link, `assignee` by accountId, `labels`, `priority`), `POST /rest/api/2/issueLink`, `POST /rest/api/2/issue/{key}/comment`, `DELETE /rest/api/2/issue/{key}`. Confluence: `POST /rest/api/content`, `DELETE /rest/api/content/{id}`. Slack: `chat.postMessage`, `chat.delete`.

## Contract tests (`connectors/tests/contract/`)
1. Seeded fixture: known docs with known ACLs, expected `fetch`, `version` and `check_access` results per persona. A real backend whose native model expresses the same ACL in other tokens (Drive on personal accounts: a group inferred from shares to each of its members, in place of their own tokens) may be compared by readers, the personas the tokens let in, instead of token by token. `check_access` is still compared exactly.
2. Revocation: remove a person from a container (channel, group, role), then `list_changes` emits one `principal_change` and `check_access` flips to deny within the SLA. Restrict a document itself, then `list_changes` emits `acl_change` for it and its `fetch` returns the new tokens.
3. Edit: change content, then `version` changes and `list_changes` emits `upsert`.
4. Inheritance: container-level permissions apply to children (Confluence space to page, Drive folder to file).
5. Negative: `check_access` for a nonexistent doc and for a forbidden doc return the same shape. `fetch` and `version` raise `DocumentNotFound` for a nonexistent doc.
6. Initial load: `list_changes(None)`, followed until `has_more` is false, returns an `upsert` for every seeded document of that source.
7. *(0.5; Jira only)* Transition: move a seeded issue as its assignee, then the returned `Document` has the new `status` field and `Status:` line and a new `version`; `fetch` and `version` agree with it; `list_changes` emits one `upsert` for it; nobody's `check_access` result has changed. A move the workflow does not offer raises `TransitionRefused` and leaves `version` unchanged. A nonexistent issue raises `DocumentNotFound`.
8. *(0.5; Jira only)* Fields: `due`, `priority` and `assignee_email` match the seed, and are absent (not empty strings) on an issue that has none.
9. *(0.6)* Create: create in a seeded destination as a person allowed to; the returned `Document` is fetchable, `fetch` and `version` agree with it, `list_changes` emits one `upsert` for it, and nobody's `check_access` on any other document has changed. The same idempotency key a second time creates nothing and returns the same `doc_id`.
10. *(0.6)* Refused create: `can_create` is false and `create` raises `WriteRefused` for a person without write permission. A destination that does not exist gives `can_create` false, the same as one the person cannot see.
11. *(0.6)* Audience: `audience` on each seeded destination matches the seed's readers, person by person.
12. *(0.6)* Unbounded audience: a link-shared folder or a destination with an unmapped or external reader returns `principals=None` or `has_external=True`, never a partial list.

## Changelog
- 0.6 (14 Oct, not built yet): `CreatingConnector` (`can_create`, `audience`, `create`, `delete_created`), `Audience`, `CreateOp` and `WriteRefused`; "Writing: creating items", with per-person write grants (Slack `chat:write`, Drive `drive.file`) and the simulator create endpoints; contract tests 9 to 12. The version line now reads 0.6 (it still said 0.4 after 0.5). Why: actions in Cortex (ADR-010).
- 0.5 (12 Oct): Jira `fields` gain `due`, `priority` and `assignee_email`, with `Due:` and `Priority:` lines in `body`; `WritableConnector.transition` and `TransitionRefused` for the Jira connector, with the one write scope it needs; contract tests 7 and 8. Why: My Work and Team focus need deadlines, and a person should be able to move their own ticket from the board without the board and Jira ever disagreeing.
- 0.4, 11 Oct: `Document.fields` (`dict[str, str]`, default `{}`) and the `documents.fields` column. The Jira connector fills `status` and `assignee` from the issue's own fields; the other connectors return `{}` for now. Why: a grounding model cannot be trusted to tell "Status: In Progress" from "the status is Done" (fix F23), and text that code must parse back into values is not a contract. The `Status:` and `Assignee:` lines stay in `body`. Contract test 1 now checks the field against the seed. `db/init.sql` has the column; ingestion adds it at start-up to a database created earlier.
- 0.3, 11 Oct addition: a Jira issue's `body` carries `Status: <name>` and `Assignee: <display name>` lines (the status line was already written; the assignee line and the simulator's `assignee` field are new). No change to the interface.
- 0.3, 11 Oct addition: Slack DMs and group DMs are documents (same thread unit and `doc_id` shape, conversation IDs may start with `D`); `check_access` on a person-private document uses the asker's own credential. Rules in acl-model.md, "Slack DMs". No change to the interface.
- 0.3, 7 Oct addition: `CursorExpired` from `list_changes`; the consumer crawls again. Raised by the simulators' connectors; Slack and Drive resync on their own and never raise it.
- 0.3, 6 Oct addition: contract test 1 may compare a real backend's ACL by readers when its native model uses other tokens for the same people (real Drive's inferred groups). No change to the interface.
- 0.3: the `chunks` schema agreed (`db/init.sql`): new `source` column, `embedding` may be null; new section "Reading the index" with the rules and reference queries for B's retrieval.
- 0.2: `list_changes(None)` defined as a full crawl; `DocumentNotFound`; `PlatformIdentity.groups` are ACL tokens and `email` is the canonical email; `Document.author` may be None; new `principal_change` change type with `principal` and `token`; document granularity and ID table, seed manifest for real backends; chunks removed on delete; `AccessDecision` stays in the policy plane; contract tests 2 and 5 updated, test 6 added.
- 0.1: first draft.

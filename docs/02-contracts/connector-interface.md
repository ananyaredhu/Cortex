# Connector interface

version: 0.3
Producer: Workstream A. Consumers: B (ingestion, PDP, JIT checks).

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
| Google Drive | One file | `gdrive:<file id>` | `gdrive:<folder id>` | File version or modified time |
| Confluence | One page | `confluence:<space>/<page id>` | `confluence:<space>` | Page version |
| Jira | One issue, including its comments | `jira:<issue key>` | `jira:<project key>` | Updated timestamp |

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
- Credentials are read-only, least-privilege, and live only in the connector process.

## Contract tests (`connectors/tests/contract/`)
1. Seeded fixture: known docs with known ACLs, expected `fetch`, `version` and `check_access` results per persona. A real backend whose native model expresses the same ACL in other tokens (Drive on personal accounts: a group inferred from shares to each of its members, in place of their own tokens) may be compared by readers, the personas the tokens let in, instead of token by token. `check_access` is still compared exactly.
2. Revocation: remove a person from a container (channel, group, role), then `list_changes` emits one `principal_change` and `check_access` flips to deny within the SLA. Restrict a document itself, then `list_changes` emits `acl_change` for it and its `fetch` returns the new tokens.
3. Edit: change content, then `version` changes and `list_changes` emits `upsert`.
4. Inheritance: container-level permissions apply to children (Confluence space to page, Drive folder to file).
5. Negative: `check_access` for a nonexistent doc and for a forbidden doc return the same shape. `fetch` and `version` raise `DocumentNotFound` for a nonexistent doc.
6. Initial load: `list_changes(None)`, followed until `has_more` is false, returns an `upsert` for every seeded document of that source.

## Changelog
- 0.3, 7 Oct addition: `CursorExpired` from `list_changes`; the consumer crawls again. Raised by the simulators' connectors; Slack and Drive resync on their own and never raise it.
- 0.3, 6 Oct addition: contract test 1 may compare a real backend's ACL by readers when its native model uses other tokens for the same people (real Drive's inferred groups). No change to the interface.
- 0.3: the `chunks` schema agreed (`db/init.sql`): new `source` column, `embedding` may be null; new section "Reading the index" with the rules and reference queries for B's retrieval.
- 0.2: `list_changes(None)` defined as a full crawl; `DocumentNotFound`; `PlatformIdentity.groups` are ACL tokens and `email` is the canonical email; `Document.author` may be None; new `principal_change` change type with `principal` and `token`; document granularity and ID table, seed manifest for real backends; chunks removed on delete; `AccessDecision` stays in the policy plane; contract tests 2 and 5 updated, test 6 added.
- 0.1: first draft.

-- Shared schema (docs/02-contracts/connector-interface.md, audit-event-schema.md, ADR-006).
-- `documents`, `chunks` and `acl_snapshots`: agreed shape, connector-interface 0.3 ("Reading the index").
-- `audit_events` is still B's draft. Changes need all three reviewers.
-- Loaded automatically by docker compose on first start.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS documents (
    doc_id        text PRIMARY KEY,                 -- "<source>:<native id>"
    source        text NOT NULL CHECK (source IN ('slack','gdrive','confluence','jira')),
    kind          text NOT NULL,
    title         text NOT NULL,
    url           text NOT NULL,
    parent_id     text,
    links         text[] NOT NULL DEFAULT '{}',
    author        text,
    created_at    timestamptz,
    updated_at    timestamptz,
    version       text NOT NULL,
    deleted       boolean NOT NULL DEFAULT false
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id           text PRIMARY KEY,                -- "<doc_id>#<position>"
    doc_id             text NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    source             text GENERATED ALWAYS AS (split_part(doc_id, ':', 1)) STORED,   -- for per-source fan-out
    position           integer NOT NULL,
    text               text NOT NULL,
    acl_tokens         text[] NOT NULL,             -- effective allow tokens (acl-model.md)
    acl_snapshot_hash  text NOT NULL,
    source_version     text NOT NULL,               -- documents.version at index time
    embedding          vector(1024),                -- bge-m3 (ADR-002); NULL when indexed without a model
    embedding_model    text NOT NULL,
    embedding_version  text NOT NULL,
    ingested_at        timestamptz NOT NULL DEFAULT now(),
    tsv                tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED
);

-- ACL prefilter: chunk.acl_tokens && asker_tokens
CREATE INDEX IF NOT EXISTS chunks_acl_gin ON chunks USING gin (acl_tokens);
CREATE INDEX IF NOT EXISTS chunks_tsv_gin ON chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS chunks_doc_idx ON chunks (doc_id);
CREATE INDEX IF NOT EXISTS chunks_source_idx ON chunks (source);
CREATE INDEX IF NOT EXISTS chunks_embedding_idx ON chunks USING hnsw (embedding vector_cosine_ops);

-- Bi-temporal ACL snapshots for replayable audit and time-travel queries
CREATE TABLE IF NOT EXISTS acl_snapshots (
    doc_id        text NOT NULL,
    snapshot_hash text NOT NULL,
    tokens        text[] NOT NULL,
    native        jsonb NOT NULL,
    valid_from    timestamptz NOT NULL,
    valid_to      timestamptz,
    PRIMARY KEY (doc_id, valid_from)
);

-- Append-only audit log (hash chain: audit-event-schema.md)
CREATE TABLE IF NOT EXISTS audit_events (
    seq        bigserial PRIMARY KEY,
    ts         timestamptz NOT NULL,
    request_id text NOT NULL,
    event_type text NOT NULL,
    actor      jsonb NOT NULL,
    event      jsonb NOT NULL,
    prev_hash  text NOT NULL,
    hash       text NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS audit_actor_idx ON audit_events ((actor->>'user_id'), ts);

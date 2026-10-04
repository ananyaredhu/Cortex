-- Ingestion's own state, created at start-up if missing. Kept out of db/init.sql (the shared draft schema)
-- until the team folds it in.

CREATE TABLE IF NOT EXISTS ingestion_cursors (
    source      text PRIMARY KEY,                   -- slack | gdrive | confluence | jira
    cursor      text NOT NULL,                      -- opaque, from Connector.list_changes
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Outbox of permission events for Workstream B (see README.md here). Append-only, written only by ingestion.
-- A consumer remembers the last seq it handled and reads `WHERE seq > that ORDER BY seq`.
CREATE TABLE IF NOT EXISTS ingestion_events (
    seq            bigserial PRIMARY KEY,
    kind           text NOT NULL CHECK (kind IN ('principal_change', 'acl_change')),
    source         text NOT NULL,
    principal      text,                            -- principal_change: "user:<canonical email>"
    token          text,                            -- principal_change: the token gained or lost
    doc_id         text,                            -- acl_change: the document
    snapshot_hash  text,                            -- acl_change: hash of the new ACL (acl_snapshots)
    detected_at    timestamptz NOT NULL,            -- when the connector saw the change
    observed_at    timestamptz NOT NULL             -- when ingestion processed it
);

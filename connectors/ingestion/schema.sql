-- Ingestion's own state, created at start-up if missing. Kept out of db/init.sql (the shared draft schema)
-- until the team folds it in: nothing but ingestion reads this table.

CREATE TABLE IF NOT EXISTS ingestion_cursors (
    source      text PRIMARY KEY,                   -- slack | gdrive | confluence | jira
    cursor      text NOT NULL,                      -- opaque, from Connector.list_changes
    updated_at  timestamptz NOT NULL DEFAULT now()
);

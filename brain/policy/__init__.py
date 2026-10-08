"""The policy plane (Zone 2). Plain deterministic code: no model, no imports from brain.gateway.

- identity: the asker's access token set, from every connector's `resolve_identity`.
- pdp: just-in-time `check_access` per candidate document, decisions with proof paths.
- labels: ACL labels on derived artifacts.
- leakscan: the local denied-set scan of an answer.
- events: the consumer of ingestion's outbox, which drops caches.
"""

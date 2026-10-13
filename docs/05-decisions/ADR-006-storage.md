# ADR-006 · Storage: single Postgres with pgvector

**Status:** Accepted.

## Context
We need vector search, keyword search, ACL-token filtering, a transactional audit store and ACL snapshots with validity intervals, on a small deployment.

## Decision
One **Postgres** instance with: `pgvector` for vectors, built-in full-text search for BM25-like retrieval, a **GIN index on `acl_tokens[]`** for the prefilter (array overlap), and tables for the audit log and bi-temporal ACL snapshots. Hosted on a Tencent Cloud Singapore VM (Lighthouse or CVM). TencentDB for PostgreSQL is an option if it supports pgvector.

## Consequences
- One system to run, secure and back up; the ACL filter and the vector search happen in one query.
- Less specialized than a dedicated vector database. Tencent Cloud VectorDB (hybrid search with scalar filtering) is a possible later swap, not needed at our scale.
- The `chunks` schema is shared between A (writes) and B (reads): see [connector-interface](../02-contracts/connector-interface.md).

## To verify
pgvector availability on the chosen Tencent service; index performance at 12k+ pages.

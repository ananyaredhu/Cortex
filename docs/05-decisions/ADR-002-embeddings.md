# ADR-002 · Embeddings: local bge-m3 by default

**Status:** Accepted.

## Context
Hunyuan embedding is 1024-dimensional with a 5 requests/s default limit, and was listed only in China regions. Hosted embeddings would send restricted content to a third party.

## Decision
Use **bge-m3 run locally** as the default embedder. Keep Hunyuan embedding as an optional backend if it proves reachable from Singapore. Alternatives if quality or speed disappoints: Qwen3-Embedding (local), or a small model (multilingual-e5-small or bge-small) on a CPU-only server.

## Consequences
- Restricted content never leaves our environment to be embedded; no rate limit; reproducible.
- CPU cost on the deployment server: pre-embed large corpora offline; measure it.
- Store `embedding_model` and `embedding_version` with every vector. Switching models means re-embedding.
- **Vectors carry the same ACL tokens as their chunk.** Embeddings can leak information, so they follow the same rules as the text.
- Optional local reranker (`bge-reranker-v2-m3`) on the top results only, if time allows.

## To verify
bge-m3 license (we recall MIT) and CPU throughput; Hunyuan embedding reachability.

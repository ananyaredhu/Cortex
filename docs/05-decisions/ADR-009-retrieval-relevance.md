# ADR-009 · Retrieval relevance at scale: search titles too

**Status:** Accepted (11 Oct). Measured with `python -m brain.relevance.run`. Keyword leg only; no real embeddings exist at scale.

## Context
The scale run showed access correctness and speed on 12,000 pages. It did not show whether the right document reaches the candidates. The permission check sees only the top 8 candidates, so a right document that is not among them is an answer the Brain cannot give.

## Method
Company A's 18 documents plus a growing prefix of the generated company (`simulators/scale.py`): 1,000, 4,000 and 12,000 Confluence pages and their Slack threads, in a throwaway database, with each persona's real tokens. 43 labeled questions with known right documents (`brain/relevance/questions.json`): 19 natural, 14 paraphrases, 3 keyword, 2 ticket-key, 5 golden. Real retrieval code (`hybrid_search`, 8 candidates, vector leg off). The generated text is filler from a 100-word vocabulary, so every question's words are common among the distractors: **harsher than real prose**. By construction the filler holds nothing relevant, so the labels are exact.

## Findings
1. **Size is not what hurts.** From 1,162 to 13,748 documents the baseline barely moves (hit@8 0.84, MRR 0.61 throughout). What costs is ranking and a gap in the index.
2. **A document's title is not in its chunks.** The title holds ticket keys (`DBMIG-150`, `SEC-17`) and the most distinctive words ("Payment-service incident runbook"), so questions naming a key found nothing (hit@8 0.00 for the two key questions), and short precise documents lost to long filler pages that repeat common words.
3. **Length normalization of the keyword rank** (`ts_rank_cd` flag 2) gave a small gain alone (hit@8 0.84 to 0.88) and **none with the title leg**: not shipped.
4. **Title weight.** Counting a title match more than a body match in the rank fusion (1.5, 2, 3) is worse than equal weight (hit@8 0.91 against 0.95): title-only matches crowd out body evidence. Kept at 1.

## Decision
Add a **title leg** to retrieval (`title_search`, fused by reciprocal rank like the other legs, same ACL prefilter, same 2-term threshold, a hyphenated ticket key counts as a whole term worth two). A GIN index on `to_tsvector('english', title)` in `db/init.sql` (a database created before this change needs `CREATE INDEX IF NOT EXISTS documents_title_tsv ON documents USING gin (to_tsvector('english', title));`; without it the leg still works, with a scan).

## Results (hit@k: a right document is in the top k; MRR over 43 questions; baseline to title leg)
| Documents | hit@1 | hit@3 | hit@8 | recall@8 | MRR |
|---|---|---|---|---|---|
| 18 | 0.67 to 0.77 | 0.91 to 0.98 | 0.93 to 0.98 | 0.91 to 0.95 | 0.79 to 0.87 |
| 1,162 | 0.42 to 0.70 | 0.81 to 0.95 | 0.84 to 0.95 | 0.80 to 0.92 | 0.61 to 0.82 |
| 4,594 | 0.42 to 0.72 | 0.81 to 0.93 | 0.84 to 0.95 | 0.80 to 0.92 | 0.61 to 0.82 |
| 13,748 | 0.42 to 0.72 | 0.81 to 0.93 | 0.84 to 0.95 | 0.80 to 0.92 | 0.61 to 0.82 |

At 13,748 documents, by kind (hit@8, baseline to title leg): natural 0.95 to 1.00, golden 0.80 to 1.00, ticket key 0.00 to 1.00, keyword 1.00 to 1.00, paraphrase 0.79 to 0.86. The golden questions all reach the candidates now. Query time at 13,748 documents is about 90 to 350 ms for retrieval depending on the cache, and the title leg adds about 10 to 20 ms.

## What is left, and why it is not solved here
- **Paraphrases with no shared words** ("How did the breach happen?" for a report saying "leaked credential in a build log") need meaning, not words. They miss with the vector leg off (2 of 43 questions). Only real embeddings can fix them.
- **The vector cutoff 0.45** (tuned on 18 documents) is untested at scale: the scale corpus was embedded with a fake embedder, and a real run is about 8 hours of CPU (`python -m connectors.ingestion.scale_run --embedder bge-m3 --reset`). Backlog T23 plans it: embed a slice, then compare the fixed cutoff with a relative one (a margin from the best distance, or a fixed count by rank).
- **A reranker** (`bge-reranker-v2-m3`) over the top candidates, only if recall is fine and ordering poor: recall@8 is now 0.92 and hit@1 0.72, so it is the next lever after embeddings.
- The filler is harsher than real prose; real corpora may do better, and the numbers should be read as a floor for the keyword legs, not a forecast.

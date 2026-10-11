# ADR-003 · Layered answer checker

**Status:** Accepted.

## Context
Even with correct retrieval, the generator might add unsupported claims or echo content it should not have. A checker from the same model family as the generator would share its blind spots. DeepSeek-R1 as a fallback checker is the same family and slow.

## Decision
Three layers behind the model gateway, cheapest and most independent first.
1. **Deterministic checks (always):** every cited source exists and is visible to the asker; a **local denied-set leakage scan** compares the answer with documents the user was denied (names, IDs, phrases, and optionally local bge-m3 similarity). No LLM involved.
2. **Small purpose-built grounding model, run locally:** MiniCheck-style claim-versus-document verification, from a different family than the generator.
3. **Small open-weight chat model for hard cases only:** Qwen 3B to 8B, or open Hunyuan small models.
If support is missing at any layer: abstain or redact.

## Consequences
- The denied-set scan is **always local**; denied content is never sent to any model or third party.
- **What "denied set" means (fix F5, 11 Oct):** every live document the asker's tokens do not reach (the ones the prefilter keeps out of retrieval), plus any candidate the live check refused. Before the fix only refused candidates were compared, a set that is almost always empty, so "clean" overstated what was checked. The log now records `checks.leak_scan_docs`. Entries are prepared once per document version (shingles and canaries) and reused. A test with a generator that leaks a canary through an allowed citation proves the scan catches it, and a negative control proves it would not have before.
- The claim check only ever sees authorized content, so a hosted model would be acceptable for layer 3 if CPU is too slow.
- Time layers 2 and 3 on the deployment server's CPU on Day 3; use a 3B to 4B model if 8B is too slow.
- Canary strings in restricted documents make the leak scan testable in Leak-CI.
- **A gray band under the threshold (fix F21, 11 Oct).** Layer 2 under-scores a true claim against a short, informal Slack message (18 to 30 permille on real data, under the 0.05 threshold), which dropped the Slack half of scenario 1 in 3 of 5 runs. A claim scoring between `GROUNDING_FLOOR` (default 0.01) and the threshold is now kept and marked `confidence: "lower"`; under the floor it is dropped as before; a floor equal to the threshold restores the old behaviour. **The 0.01 floor is a judgement, not a measurement:** it sits under every observed false rejection and above the near-zero scores an invented claim should get, but the labeled set (40 pairs) has no distribution of invented claims at that scale. Re-measure with the bigger set (backlog T14) and move the floor if invented claims appear in the band. Trade-off accepted: a few more unsupported claims may be shown, labeled, in exchange for a reliable demo.

## Measured (check #2, 9 Oct, this laptop: i5-8250U, 4 cores, 8 GB, `python -m brain.checker.timing`)
40 labeled pairs from fixture text (a sentence against its own document = supported, against another = unsupported).

| Model | Family | ms per pair | Peak memory | Accuracy | Separation |
|---|---|---|---|---|---|
| `cross-encoder/nli-deberta-v3-xsmall` | DeBERTa-v3 NLI | 44 | 0.9 GB | 0.90 at threshold 0.05 (0.80 at 0.5) | unsupported never above 0.023; supported median 0.95 |
| `cross-encoder/nli-deberta-v3-small` | DeBERTa-v3 NLI | 141 | 1.5 GB | 0.72 at any threshold | scores collapse near 0 for this input length |
| `lytang/MiniCheck-DeBERTa-v3-Large` | MiniCheck | 1,341 | 2.0 GB | 0.97 at 0.5 | supported mean 0.79, unsupported 0.07 |

**Decision:** layer 2 runs `cross-encoder/nli-deberta-v3-xsmall` with threshold 0.05 (`CHECKER_MODEL`, `GROUNDING_THRESHOLD`):
about 0.2 s for a five-claim answer and under 1 GB. MiniCheck-DeBERTa-v3-Large is the better judge but costs
1.3 s per claim and 2 GB, so it is reserved for offline grading (Leak-CI, red-team scoring) and for hard cases
once a server with memory to spare exists. Layer 3 (a 3B to 8B chat model) was not timed: a 4-bit 3B model needs
about 2 GB and a runtime this laptop does not have alongside bge-m3 and the API; measure it on the deployment
server, or use a hosted small model from a different family than the generator.

## To verify
Whether open Hunyuan small models are available for layer 3; MiniCheck on the deployment server's CPU.

# ADR-001 · Model gateway and model tiers

**Status:** Accepted. Final model choices are set after the Day-2 probe (owner: B).

## Context
Two separate LLM uses exist.
- **Build time:** the models inside CodeBuddy and WorkBuddy, paid for by the 1,000 credits per person. WorkBuddy's picker shows Hy3 and Hy4 preview at 0.00x, Fast 0.34x, Balanced 0.59x, Auto 0.79x, GPT-5.6-Terra 1.39x, Primary 3.31x, Deep 3.33x, GPT-5.6-Sol 3.47x. We read the multiplier as credit cost per request: confirm in the credits dashboard.
- **Run time:** the models our backend calls. Credits do not pay for these. A model's presence in the WorkBuddy picker does **not** mean the deployed app can call it.

## Decision
A **model gateway**: one interface, config-driven backends, tiered by task (Atlassian-style hybrid use).

| Task | Starting choice | Notes |
|---|---|---|
| Routing, query rewrite, relevance check | Small, fast: Hunyuan (OpenAI-compatible endpoint), else DeepSeek-V3 with short prompts | Hunyuan access from Singapore: verify |
| Answer generation (structured claims plus source IDs) | DeepSeek-V3 via ADP or TokenHub (OpenAI-compatible) | Check context length and structured-output support |
| Answer checker | See [ADR-003](ADR-003-checker.md) | Different family from the generator |
| Offline grading and red-team scoring | DeepSeek-R1 | Too slow for the live path |
| Embeddings | See [ADR-002](ADR-002-embeddings.md) | |

Build-time guidance: Hy3 and Hy4 preview (0.00x) for routine work; Fast, Balanced or Auto for medium tasks; keep the roughly 3x models for hard design and review.

## Consequences
- Models can be swapped without touching the pipeline.
- A **Day-2 probe script** measures per candidate: reachability from Singapore, latency, rate limits, structured-output or function-calling support, context length, cost. The result updates this ADR.
- Token budget is a risk: cache repeated calls, use the small model where enough, run Leak-CI as a separate batch.
- Only authorized content is ever sent to a hosted model. Denied content never leaves the policy plane.

## Probe results
- **9 Oct, Tencent Cloud ADP, agent model GPT-5.6 Terra** (`python -m brain.gateway.probe`, 3 rounds from this laptop): reachable; 3 of 3 replies parsed as JSON claims with every citation naming a packet document; latency 2.9 s, 3.5 s and 13.9 s per call. Called through the Chat API by AppKey (`brain/gateway/adp.py`), with our claims instruction in `SystemRole` and online search disabled per turn. This is the generator in use. Context length, rate limits and cost not yet measured; the agent must stay without a knowledge base.
- Hunyuan, TokenHub, LKEAP: not probed.

## To verify
Hunyuan and TokenHub access from Singapore; free allowances and rate limits; whether ADP's built-in DeepSeek is reachable as a raw completion endpoint (its Chat API calls a published ADP app with its own prompt, so we call model endpoints directly and treat ADP as optional).

# 06 · Risks and week-1 checks

Everything marked **verify** comes from web searches or the handbook and has not been confirmed in our own accounts. Resolve these in week 1 (by Day 5, Tue 6 Oct) and record the result here.

## Checks
| # | Check | Owner | Due | Result |
|---|---|---|---|---|
| 1 | **Model probe:** run a script against each candidate model and record: reachable from Singapore, latency, rate limits, structured-output / function-calling support, context length, cost. Candidates: DeepSeek-V3 (ADP / TokenHub), Hunyuan (OpenAI-compatible), Qwen, small Hunyuan open weights | B | Day 2 | |
| 2 | **Checker timing:** time the layer-2 grounding model and a 3B-8B chat model on the deployment server's CPU | B | Day 3 | |
| 3 | **Slack:** custom internal app in our own workspace is exempt from the 2025 rate-limit cut (1 request/min, 15 objects) that targets commercially distributed non-Marketplace apps | A | Day 2 | **Exempt from the 1 request/min limit** (4 Oct, `python -m connectors.slack.check_rate_limit`): 12 back-to-back `conversations.history` calls in 4.1 s, 0 rate-limited. The 15-objects cap is not yet confirmed: the seeded channels hold fewer than 15 messages. Re-run on a channel with more than 15 messages |
| 4 | **Google Drive:** per-user OAuth with several free Google accounts; `changes.watch` push notifications reach our webhook | A | Day 3 | **Per-user OAuth works** (5 Oct): one OAuth client, Priya, Dana and Maya signed in; `python -m connectors.gdrive.check_setup` matches the fixtures and all 10 persona-by-file access checks are right. **`changes.watch` works** (6 Oct, through a temporary Cloudflare tunnel): Google accepted a channel per account and its notifications reached the receiver; an edit reached the index about 18 s later. No domain verification needed, only a valid HTTPS certificate. Found on the way: a Google Doc's modified time moves once per editing session, so the connector now uses Drive's `version` counter |
| 5 | **Atlassian free plan:** reportedly no space/page or project/issue permissions. Decide: paid-plan trial window, sandbox from Tencent/Aspire, or simulator only. Check trial length and which plan includes issue security | A | Day 2 | **Simulator only** (decided Day 2). No trial or sandbox is used. Confluence and Jira are simulated with space permissions, inherited page restrictions, project roles and issue security levels; both pass the shared contract tests. See `simulators/README.md` |
| 6 | **Embeddings:** bge-m3 speed on CPU; license check (MIT as we recall). Hunyuan embedding reachable? (listed in China regions, 5 requests/s) | B | Day 3 | **bge-m3 works on CPU** (6 Oct, run by A, `python -m connectors.ingestion.embed_check --repeat 5`, laptop with i5-8250U, 4 cores, 8 GB RAM): about 2.4 chunks/s (0.41 s per chunk, batch 8), 1024 dimensions as `chunks.embedding` expects, peak memory about 1.9 GB, model load 31 s warm. First run downloads about 4.3 GB into `HF_HOME`. License **MIT** (model card). At this speed the 12k-page scale corpus needs hours, so pre-embed it offline. Hunyuan embedding not checked |
| 7 | **WorkBuddy MCP:** remote HTTP MCP with an `Authorization` header works end to end; scheduling works | C | Day 4 | |
| 8 | **CodeBuddy MCP config:** `.mcp.json` with `type: http` and headers using env vars works; confirm whether it reads `CODEBUDDY.md` or `AGENTS.md` | C | Day 2 | |
| 9 | **Sandbox:** CubeSandbox (or Tencent "Agent Runtime") availability and credits; fallback Docker with network policy | C | Day 4 | |
| 10 | **Tencent hosting:** Lighthouse/CVM in Singapore, cost and credits for a live demo URL | C | Day 3 | |
| 11 | **Build-time credits:** what the multipliers in WorkBuddy mean (Hy3, Hy4 preview show 0.00x; Fast 0.34x, Balanced 0.59x, Primary 3.31x, Deep 3.33x, GPT-5.6 models 1.39x to 3.47x); remaining balance per person | all | Day 1 | |
| 12 | **Ask in the track WhatsApp group:** are mock/simulated connectors acceptable? the track-specific judging criteria; the correct submission link (the handbook and the credits page show two different links) | C | Day 1 | |
| 13 | **GitHub:** repo visibility or judge access plan for submission; license choice | all | Day 12 | |

## Risks
| Risk | Impact | Mitigation |
|---|---|---|
| Schedule: about 45 person-days of work in 15 days | High | Tiers define build order; demoable build by Day 8; schedule review on Day 8; stubs by Day 3 |
| Atlassian free plan lacks permissions | High (permission semantics are the core) | Faithful simulator plus contract tests; optional trial window for a real recording |
| Model access or quota from Singapore | Medium | Model gateway with swappable backends; probe on Day 2; cache and batch eval runs |
| Token budget for Leak-CI and evals (hundreds of queries x several calls) | Medium | Cache repeated calls; small model where enough; run evals as a separate batch |
| CPU-only server too slow for local checker/embeddings | Medium | Smaller models (3B-4B, small embedder); pre-embed offline; measure on Day 3 |
| Prompt injection or denied-content leak in testing | High | Layered defenses; Leak-CI as a release gate; local denied-set scan |
| Secrets in screenshots or commits | High | `.env`, gitignore, pre-commit scan, review screenshots before logging them |
| Contracts change late and break other workstreams | Medium | Freeze Day 3; changes need all three reviewers |

## Day-1 decisions made
- Real Slack and Drive connectors; Confluence and Jira simulated with faithful permission semantics (optionally a real free Atlassian tenant for content and webhooks only).
- Local bge-m3 embeddings by default; Hunyuan optional.
- Layered checker, with the denied-content scan always local.
- Stateless MCP.
- Nothing is cut from scope; tiers are build order.

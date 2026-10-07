# Mockup vs repo: drift and resolutions

The Claude Design mockup (`design-reference/`) was made from the design brief (`design-reference/design-brief.md`) before it met the repo. This file lists every place where it disagrees with the contracts, fixtures, stub API, scenarios or AGENTS rules, and what we do about it.

**Rule of thumb:** where the mockup conflicts with a security rule or a contract, the rule wins and the screen changes. Where it only needs data the API doesn't have yet, the field goes into the contract as optional and the UI hides that part until it arrives.

Status: **Done** = handled in the Phase 0 branch (`ws-c/ui-phase0`). **Proposed** = in the api.md 0.2 PR, waiting for approval. **Ask X** = needs the named workstream. **Build** = for the phase named.

## Identity and content

| # | Drift | Resolution | Status |
|---|---|---|---|
| 1 | Two visual systems in one zip: **Cortex** (`Workspace.dc.html`, orange, Helvetica + Newsreader) and **Internal Brain / Pathfin** (Ask, Audit, Admin, Mobile, Cover: blue, IBM Plex). | Cortex everywhere. Keep the Pathfin screens' *layouts* and *states*; re-skin them with the Cortex tokens and components. Ignore `Workspace v1 (IBM Plex)` (left out of `design-reference/`). | Done (decision); Build (re-skin per screen) |
| 2 | Product name: the mockup says Cortex; the repo, docs and handbook say "The Internal Brain". | Cortex is the product name in the UI and the submission (decided 7 Oct). The presentation still names the case study ("The Internal Brain") at the start, as the handbook asks. The repo keeps its name. | Done (decision) |
| 3 | Fictional tenant and people: Pathfin with Alice Tan, Ben Ong, Raj Verma, Chan Kai, Mei Lim, Wei Zhang. Workspace uses full names (Priya Nair, Sam Reyes...). | Company A personas from `fixtures/company_a.json`: Alice → **Priya**, Raj → **Sam**, Mei Lim → **Dana**, Chan Kai → **Jordan**, plus **Maya**. No persona for Ben (new joiner) or Wei (CTO): Dana's Admin view covers the CTO's "trust at a glance". Show `display_name` from the API, not invented surnames. | Done (`src/auth/personas.ts`) |
| 4 | Scenario 5 (handbook and mockup) asks about user **jdoe** in a "payment-gateway" space; neither exists in the fixtures. | Query **Priya** in the `PAY` space instead: the scenario shows that Jordan can reconstruct any employee's history, not who that employee is. Priya's earlier demo questions supply the history. `docs/04-scenarios.md` updated. | Done (decision, 7 Oct) |
| 5 | Sample content (19 May outage, pool 200 → 50, PAY-2110, PAY-412 settlement retry, CONF-88213, `#payments-oncall`, the PGW space) is not in the fixtures. | The UI never hardcodes content: everything comes from the API. The fixtures already tell the same stories under other IDs (PAYINC-9/10/11 for the outage, `confluence:PAY/...`, `#payments-incident`). If the video needs the richer story, A extends `fixtures/generate.py`, not the UI. | Done (no hardcoded content) |
| 6 | Persona SVGs carry C2PA provenance metadata (about 6 KB each). | Harmless; keep. Strip later if bundle size matters. | — |

## Ask

| # | Drift | Resolution | Status |
|---|---|---|---|
| 7 | **Trust panel shows "found → allowed → shown" per platform** (for example Confluence 12 → 9 → 1). The found-minus-allowed gap is a denied count: Sam asking about the breach report would see "Confluence 1 → 0", which confirms it exists. This breaks scenario 3, AGENTS rule 7, api.md ("no counts, no titles") and would fail Leak-CI's add/remove-hidden-document tests. | Show only `coverage`: per source, *searched* and *shown*. The contract now forbids candidate or denied counts in any asker-facing response, and a test checks it. Denied counts stay in the Audit console (compliance only). Rename "Why can't I see more?" to a static explainer ("Cortex only reads what you can open yourself...") with no numbers. | Proposed (api.md 0.2 `coverage`); tested (`evals/tests/test_api_v02_stub.py`); UI done (Trust panel) |
| 8 | Coverage tile: "1,412 items in scope for you". | It counts only what the user can read, so it is safe, but nothing provides it. Leave it out; add `in_scope_count` to `/mywork` later if wanted. | Later |
| 9 | Thinking stepper (5 named steps) has nothing behind it: the API has no streaming, so it would be a timed animation in a product about trust. | New `POST /v1/ask/stream` (SSE) sends the real stages. All five stages are always sent, in order, for every request, so the stream reveals nothing. The Phase 0 Ask screen already uses it. | Proposed; stub done; tested; UI done (stepper) |
| 10 | Hover popover with a 2-line excerpt per citation. | `citations[].excerpt` (sanitized, up to 280 characters). Render it as a quoted excerpt, never in the assistant's voice. | Proposed; stub done; UI done (popover on marker hover or focus) |
| 11 | Grounding score (96%) and "1 unsupported statement was removed". | `grounding: {score, removed_claims}`, filled by B's checker (ADR-003). The stub always says 1.0. | Proposed; Ask B |
| 12 | Policy version as "policy 3c9f · 2 Oct". | `policy_version` as an opaque string (the audit schema uses `pol-0.3`). Show it as-is, without a date. | Proposed |
| 13 | Freshness SLA: the brief uses green < 15 min and amber < 60 min. The repo's pass bar is **under 5 minutes for events, under 1 hour worst case** (`docs/04-scenarios.md`). | The server decides `freshness.per_source[].status` (`ok`, `stale`, `unavailable`) against the repo's SLA; the UI only maps status to colour + label and never computes thresholds itself. | Proposed |
| 14 | Stale banner ("Slack data is 2 h old...") and error banner ("source unreachable"). | Driven by `per_source.status == "stale"` and `unavailable_sources`. The stub never produces either: add a `/sim` switch for them when Phase 1 builds the banners. | Proposed; stub switch done (`/sim/source-status`); UI done |
| 15 | Ambiguous question → clarifying choices. | `clarify: {question, options}`, options built only from allowed documents. If B doesn't build it, drop the state from the demo. | Proposed; Ask B |
| 16 | Platform scope chips and time-range chip on the composer. | Request fields `sources` (the stub honours it) and `time_range` (the stub ignores it). Hide the time chip until B implements it. | Proposed; stub partly; UI done (scope chips, no time chip) |
| 17 | Conversation history grouped by day. | `GET /v1/conversations`. The Phase 0 sidebar already lists them; grouping by day is client-side. | Proposed; stub done; UI done (sidebar list; grouping by day still to do) |
| 18 | Guest badge "Guest access · Nimbus Consulting · 1 channel, 1 folder". Sam has **no Slack account** (PR #12), so "1 channel" is wrong, and the counts need an endpoint. | "Guest access · contractor.io", no counts. | Done |
| 19 | Answer footer **Share link**. Sharing an answer passes derived content to someone who may not be allowed to see its sources (AGENTS rules 5 and 6). | Drop it. If wanted later: share the *question* only, re-run under the recipient's identity. | Done (not built) |
| 20 | Footer **Report a problem**: no endpoint. | Leave it out for now; later an audit event (`feedback`) through B. | Later |
| 21 | Microphone in the composer. | Stretch goal (TRTC voice). Hide until built. | Later |
| 22 | Refusal text: the mockup adds "If you think it should exist, ask the owner or your manager." The contract's string is "I couldn't find anything you have access to about that." | Render the server's `answer` verbatim. The extra sentence can be **static UI text shown under every refusal**, identical in every case, so it adds no signal. | Done (Phase 1) |
| 23 | Inline `[n]` markers on every sentence; the API returns prose `answer` plus `claims[]`. | Render the answer from `claims[]` (one sentence each, with its markers), not from `answer`. In dev builds, flag any claim with no citation (B's checker should never let one through). | Done (Phase 1) |

## My Work (Workspace)

| # | Drift | Resolution | Status |
|---|---|---|---|
| 24 | Needs-you list, ticket priority and due dates, sprint, team, project timeline, sprint board, "Security" card. None of it is in `/v1/mywork` or the fixtures. | Build only what `/mywork` returns: issues (with optional `status`), projects, channels, recent pages, suggested questions, alerts. Drop the rest. | Build (Phase 4) |
| 25 | "Synced 2 min ago" on the home page; `/v1/freshness` is admin-only. | Take it from the latest answer's `freshness.per_source`, or add a `last_sync` summary to `/mywork` later. | Later |
| 26 | Hardcoded `companya.atlassian.net` and Slack links. | Use each item's `url` from the API (now optional in `/mywork`). | Proposed; stub done |
| 27 | Stale-answer alerts exist in the contract and stub but have no screen. | Put them at the top of My Work ("Needs you"), plus a count in the sidebar. They are a listed demo moment. | Build (Phase 4) |
| 28 | **Split-screen personas** (Priya, Sam and Dana ask the same question) is a planned demo moment with no mockup. | New `/compare` route: three columns, each its own request with its own persona token (never mixed client-side), each rendered with the Ask answer card. | Build (Phase 2) |
| 29 | Demo controls: only "Tamper one row" exists in the mockup. Scenarios 2 and 4 need "advance e1/e2" and reset. | A drawer behind `VITE_DEMO_CONTROLS`, calling `/sim/*` (stub) or A's simulator admin endpoints (real). Never in production builds. | Build (Phase 2) |

## Audit console

| # | Drift | Resolution | Status |
|---|---|---|---|
| 30 | "Answer text isn't stored in the log", but the audit schema stores `answer.text` (needed for replay). | Keep the schema. The console shows the hash by default and reveals the text only when the viewing officer may see every cited document. | Build (Phase 3) |
| 31 | Grounding column and per-stage timestamps per request; the audit event has `checks` and `latency_ms` only. | Ask B to add `grounding` and `stages: {stage: ms}` to the audit event schema (B's contract, not changed here). Hide the columns until then. | Ask B |
| 32 | Space filter "Confluence › PGW": there is no PGW space. | Use the fixture spaces (`PAY`, `ENG`, `SEC`, `HR`). | Build |
| 33 | Chain widget: "10,482 entries · last checkpoint signed 14:00". `/verify` returns `checked` and `checkpoints` but no checkpoint time. | Show `checked` and `checkpoints`; ask B for an optional `last_checkpoint_at`. | Ask B |
| 34 | Broken state: "Changed field: denied_count (3 → 0)", stored vs recomputed hash. A hash can't tell which field changed. | Drop "changed field". Show `first_broken_seq` and `reason`; ask B for optional `stored_hash` / `recomputed_hash` on failure. | Ask B |
| 35 | "Tamper one row" button: the stub has `/sim/tamper`; the real system must not have a tamper endpoint. | Stub: the demo drawer calls `/sim/tamper`. Real: B ships a demo script that edits one row in Postgres; the video shows the script, then "Verify now". | Ask B |
| 36 | Decision table shows "restricted item with its id". Denied decisions store only a **salted hash** (`doc_id_hash`), on purpose. | Show the short hash for denied rows, never an id. Allowed rows show the id, and the title only if the officer may see it. | Build (Phase 3) |
| 37 | Replay view: "what jdoe was shown" (Priya, see #4) vs "what they'd see now", with "revocation took effect 38 s after the ACL change". | `GET /v1/audit/replay` (titles only where the officer has access). The 38 s figure comes from A's `revocation_timing`; add it to replay later if wanted. | Proposed; stub done; tested |
| 38 | Per-user access timeline. | Client-side, from `/audit/query` with `filter.user`. No contract change. | Build (Phase 3) |

## Admin

| # | Drift | Resolution | Status |
|---|---|---|---|
| 39 | Policy diff in **Rego** (`policies/confluence.rego`). AGENTS rule 2: the policy plane is plain Python in `brain/policy/`. | Show the Python diff (or link to its PR via `pr_url`). Never Rego. | Proposed (`/policy/versions`) |
| 40 | Connector cards: items indexed, SLA met %, 24 h lag sparkline. A's freshness report (`connectors/ingestion/freshness.py`) has p50, p95, max, last run, last success and last error, not counts or a time series. | Build cards from what exists. Ask A to add `documents` and `sla_met_ratio` per source (cheap); drop the sparkline unless A adds hourly buckets. The stub now returns A's exact shape. | Done (stub shape); Ask A |
| 41 | SLA chart with a single 60-minute target. | Two lines: 5 min (events, p95) and 60 min (worst case), per `docs/04-scenarios.md`. | Build (Phase 3) |
| 42 | Evaluate sandbox with Alice, Ben, Raj, Mei and CONF/DRV ids. | Fixture persona emails and fixture doc ids. Admin-only and logged, so it can't be used as an existence oracle. | Proposed; stub done; tested |
| 43 | Trap tests "8 of 8 passing". | `leakci.suites` (optional) filled by C's Leak-CI runs; the stub returns four placeholder suites marked `stub: true`. | Proposed; stub done |
| 44 | Who is admin? The mockup's Mei Lim is a security lead; the fixtures have no admin role. | Admin endpoints accept `security-lead` or `compliance` (Dana, Jordan). The sidebar shows Audit to compliance, Admin to both. The API enforces it too. | Proposed; Done |

## Visual details

| # | Drift | Resolution | Status |
|---|---|---|---|
| 45 | The brief reserves one accent (blue) for interaction and green/amber/red for trust state. | Cortex orange is the accent; green/amber/red stay reserved for trust state, always paired with an icon or label (Cortex already restricts them to badges and dots). | Build |
| 46 | Contrast: white on Cortex orange fails AA. | Follow Cortex: ink text on orange (`--text-on-accent`), orange text only as `--orange-700`. | Done (tokens) |
| 47 | The brief asks for tabular figures in tables. | Add `font-variant-numeric: tabular-nums` to tables and counters when they are built. | Build |
| 48 | Cover image: "Pathfin · Internal Brain", Alice's outage answer. | Rebuild in Cortex with a real fixture answer (Priya, PAYINC outage). Blurb under 10 words. | Build (Phase 4) |

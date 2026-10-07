# Design brief: "Internal Brain" — permission-aware enterprise knowledge assistant

## What you are designing

A high-fidelity UI mockup for **Internal Brain**, a product that lets employees of a fintech ask natural-language questions across Confluence, Jira, Slack and Google Drive and get a single cited answer — while guaranteeing that every answer contains only content the asker is allowed to see, and that every retrieval is recorded in a tamper-evident audit log.

This is a hackathon submission (Tencent Cloud Hackathon Singapore 2026, FinTech track, challenge set by Aspire). The mockup will be used for the demo video, the submission cover image, and as the reference for the front-end build. Judges score on impact, human-centred design, AI interaction, UX & accessibility, responsible AI, and storytelling — the UI has to make the *trust story* visible, not just the chat.

**Core idea to express visually:** the AI is a reader with an escort. Every answer shows what was looked at, what was withheld (as counts only), how fresh the sources are, and how much of the answer is grounded in real documents. Nothing is hidden from the user about the process; nothing restricted is revealed by the process.

Design for web desktop first (1440 wide), with a responsive tablet/mobile treatment for the chat surface only. Light and dark themes.

---

## Users and their jobs

| User | Job to be done | Surface |
|---|---|---|
| **Alice Tan**, backend engineer | Find "why did payments go down last quarter and what tickets came out of it" in one question instead of four tools | Ask |
| **Ben Ong**, joined last week | Ask beginner questions without knowing which tool holds the answer; understand why some things aren't visible to him yet | Ask |
| **Raj Verma**, external contractor | Ask about the one project he's on; must never learn that restricted things exist | Ask (guest mode) |
| **Chan Kai**, compliance officer | Reconstruct what any user asked and saw; prove the log hasn't been altered | Audit console |
| **Mei Lim**, security lead | Review access policy changes; see who touched security content | Audit console + Admin |
| **Wei Zhang**, CTO | Trust that the assistant can't leak; see freshness and coverage at a glance | Admin overview |

The fictional company is **Pathfin** (Singapore B2B fintech). Use these names and the sample content below throughout — no lorem ipsum.

---

## Screens required

### 1. Ask — the employee chat (primary screen)

Three-column layout on desktop: left rail (conversations + platform filters), centre (conversation), right panel (**Trust panel**, collapsible, open by default on first use).

**Composer**
- Multi-line input with placeholder: "Ask across Confluence, Jira, Slack and Drive…"
- Optional platform scope chips (Confluence / Jira / Slack / Drive / All) — default All.
- Optional time-range chip ("Last week", "Last quarter", "Any time").
- Microphone icon for voice input (secondary).
- Send button. Keyboard hint: ⌘/Ctrl+Enter.

**Answer card** — the most important component. Show one fully populated example:

> **Question (Alice Tan):** What was the root cause of the payment outage last quarter, and what follow-up tickets were created?
>
> **Answer:** The 19 May outage was caused by connection-pool exhaustion on the payment gateway after a config change reduced the pool size from 200 to 50 [1][2]. Recovery took 47 minutes. Three follow-up tickets were created: PAY-2110 (add pool-size alerting, Done) [3], PAY-2111 (config change review gate, Done) [4], and PAY-2112 (load-test config changes in staging, still Open) [5].
>
> **Sources**
> [1] Confluence · ENG · *Payment gateway outage — postmortem* · synced 3 min ago
> [2] Slack · #payments-oncall · thread from 19 May, 14:02 · synced 1 min ago
> [3] Jira · PAY-2110 · Done · synced 2 min ago
> [4] Jira · PAY-2111 · Done · synced 2 min ago
> [5] Jira · PAY-2112 · Open · synced 2 min ago

Each citation is a chip with platform icon, title, status/date, and freshness. Hovering a citation number in the answer text highlights the matching chip and shows a 2-line excerpt in a popover. Clicking opens the source in a new tab (deep link).

Inline citation numbers must be visually distinct but not noisy; every sentence in the answer has at least one.

Answer footer actions: Copy · Share link · Report a problem · "Why can't I see more?" (opens the trust panel with the counts highlighted; never lists titles).

**Trust panel (right, per answer)** — the differentiating element. Contents, top to bottom:
1. **Sources considered**: four platform rows, each with "candidates found → allowed → shown", e.g. Confluence 12 → 9 → 1, Jira 21 → 21 → 3, Slack 34 → 26 → 1, Drive 6 → 4 → 0. Denied is expressed only as a number; a small note reads "Items you don't have access to are not shown and are not named."
2. **Freshness**: per platform, "last synced" with a green/amber/red dot against the SLA (green < 15 min, amber < 60 min, red = stale). If any source is stale, the answer card itself carries a banner: "Slack data is 2 h old — the answer may be missing recent messages."
3. **Grounding**: a single figure (e.g. 96%) with a one-line explanation "Share of statements verified against a source you can access." If any statement was removed by the grounding check, show "1 unsupported statement was removed."
4. **Policy version**: short hash + date, e.g. `policy 3c9f · 2 Oct`.
5. **Audit reference**: `req_8f3a` with a copy icon and the text "Recorded in the audit log."

**States to design for the Ask screen**
- **First run / empty**: three example questions as clickable prompts, plus a short explanation of what the assistant can and cannot see.
- **Thinking**: a stepped progress indicator that mirrors the pipeline in plain words: "Finding sources → Checking your access → Verifying with source systems → Writing answer → Checking citations." Not a spinner.
- **Uniform "no information" answer** (the negative case): "I couldn't find anything you have access to on that topic. If you think it should exist, ask the owner or your manager." The card has the same size, the same trust panel structure and the same footer whether or not a restricted document exists. Design this so a screenshot of Raj asking for the breach report is visually identical to Raj asking about something that doesn't exist.
- **Ambiguous question**: a short clarifying prompt with 2–3 choices ("Do you mean the May outage or the August one?").
- **Stale source warning** (banner variant, described above).
- **Guest mode** (Raj): a persistent, calm badge in the header "Guest access · Nimbus Consulting · 1 channel, 1 folder". No other visible difference.
- **Error**: source system unreachable — answer still returns from the other three with a banner naming the missing platform.

**Left rail**
- Conversation history grouped by day.
- Platform filters (toggle chips).
- A small "Coverage" tile: "Connected: Confluence, Jira, Slack, Drive · 1,412 items in scope for you." (The number is what the user can read, not the total in the company.)

**Mobile treatment** of Ask: single column; trust panel becomes a bottom sheet opened from a "Trust" pill on each answer; citations collapse into a horizontally scrolling chip row.

### 2. Audit console — compliance officer

Layout: query bar on top, results as a timeline, detail drawer on the right.

**Query bar**: natural-language audit question with structured filters below it (User · Platform / space · Date range · Decision: allowed / denied / all). Example query populated:
> Show me everything user jdoe accessed related to the payment-gateway Confluence space in the last 30 days.

**Results timeline**: one row per request, columns: time · user · question (truncated) · platforms hit · allowed / denied counts · grounding · chain status (✓). Rows expand into the **request detail drawer**:
- The full question and the answer hash (not the answer text) with a "Replay answer" button.
- Per-document decision table: doc id · platform · title (only if the *viewing officer* has access; otherwise "restricted item" with the id) · decision · policy version · ACL version · "re-verified live" tick.
- Timestamps for each pipeline stage.

**Chain integrity widget** (top right, always visible): "Log integrity verified · 10,482 entries · last checkpoint signed 14:00 SGT". Includes a **"Verify now"** button that animates a recomputation and a demo-only **"Tamper one row"** control that turns the widget red with "Entry 10,441 does not match its hash — chain broken at 13:52." This is the demo's key moment; design both the green and red states with care.

**Replay view**: side-by-side — what the user was shown at the time (rendered from the logged doc versions and policy) vs. what they would see now, with differences highlighted (e.g. a page since restricted).

**Per-user access timeline**: a secondary view — pick a user, see a horizontal timeline of accesses coloured by platform, with denials as hollow markers.

### 3. Admin — policy & connectors

- **Connector health**: four cards (Confluence, Jira, Slack, Drive) with status, last event received, items indexed, freshness SLA met %, and a sparkline of sync lag over 24 h.
- **Policy**: list of policy versions (hash, author, date, PR link), with a diff view of the latest change and an "Evaluate" sandbox: pick a user + a document id → shows allow/deny and the rule that decided it.
- **Freshness SLA dashboard**: a single chart of end-to-end update latency (source change → available in answers) against the 60-minute target.
- **Trap tests**: a panel listing the built-in security checks (injection, side-channel, revocation, retention) with last-run pass/fail — this makes "responsible AI" visible to judges.

### 4. Cover image (16:9, 380×216 rendered from a larger 1920×1080 frame)

Product name, one-line blurb (max 10 words — propose three options, e.g. "Ask anything. See only what you're allowed to."), and a cropped answer card with the trust panel visible. No stock imagery, no abstract brain illustrations.

---

## Components to specify (for the build)

- Answer card (default, no-information, stale-banner, error-banner variants)
- Citation chip (Confluence / Jira / Slack / Drive variants; states: default, hover, stale, revoked-since)
- Trust panel and its bottom-sheet variant
- Pipeline progress indicator (5 steps)
- Freshness dot + tooltip
- Grounding score display
- Chain integrity widget (verified / verifying / broken)
- Decision table row (allowed / denied / re-verified)
- Guest badge
- Platform icons: use simple geometric glyphs in a single colour family — do **not** reproduce the real Confluence, Jira, Slack or Google Drive logos.

---

## Content rules (these are product requirements, not copy preferences)

- Never display a title, excerpt or count *per restricted space* of anything the current user cannot access. Denials are shown only as totals per platform.
- The no-information response is one fixed string; do not vary it by cause.
- Freshness is always shown; "synced just now" is fine, but the field must exist.
- Every answer statement has a citation. If a design needs an uncited sentence to look good, the design is wrong.
- Retrieved content is never rendered as if it were the assistant speaking — quotes from sources are visually set apart (excerpt style) from the assistant's synthesis.
- Audit views show hashes and ids, not document bodies, unless the viewer independently has access.

---

## Visual direction

- Tone: calm, institutional, precise — a compliance product that people actually enjoy using. Closer to a well-designed bank back-office tool than to a consumer chatbot.
- One typeface family (a humanist sans with good numerals — numbers matter here: counts, hashes, timestamps). Tabular figures for tables.
- Colour: a neutral base with one accent for interactive elements and a **separate, reserved** colour for trust/security state (green verified · amber stale · red broken) that is never used decoratively.
- Density: comfortable in chat, dense in the audit console. Tables in the console should sit at 13–14 px with generous row height.
- Motion: only for state changes the user triggers (verify chain, expand decision row, open trust panel). No ambient animation.
- Accessibility: WCAG AA contrast in both themes; all state colours paired with an icon or label; full keyboard navigation for the composer, citation chips and console tables; visible focus rings.

---

## Deliverables

1. Ask screen — desktop, populated with the Alice example; plus the empty, thinking, no-information, stale-banner and guest-mode states.
2. Ask screen — mobile, populated, with trust bottom sheet open.
3. Audit console — populated with the jdoe query; detail drawer open; chain widget in verified state.
4. Audit console — chain widget in broken state (after "Tamper one row").
5. Replay view.
6. Admin — connector health + policy evaluate sandbox on one screen.
7. Cover image with three blurb options.
8. Component sheet with the variants listed above, light and dark.
9. A one-page "design rationale" note: how the UI makes each of the five required scenarios (unified query, freshness, negative case, live revocation, audit inquiry) visible to a judge in under ten seconds each.

If a requirement here conflicts with a nicer-looking layout, the requirement wins — say so in the rationale note rather than bending it.

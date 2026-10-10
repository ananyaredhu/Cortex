# Workstream C · Experience and proof

**Owner:** Guanyue ([@guanyue017-dev](https://github.com/guanyue017-dev)) · **Directories:** `ui/`, `evals/`, `deploy/` · **Status file:** [status/ws-c.md](../status/ws-c.md)

## Mission
Make it usable, testable, deployed and submittable. Own the demo, the zero-leak proof and the paperwork, including the CodeBuddy/WorkBuddy proof log.

## Scope
- **Mock OIDC IdP** (JWT) and personas.
- **UI:** chat with citations and "why can I see this?", persona switcher and split-screen demo, **My Work** home, admin and audit console (audit explorer, freshness dashboard, permission-graph viewer, Leak-CI scoreboard).
- **Evals:** golden tests for all seven scenarios, **Leak-CI** (noninterference tests), red-team harness (prompt injection, existence side-channels, canary strings, revocation races).
- **Deployment** on Tencent Cloud Singapore (live URL for bonus points) and the sandbox setup.
- **WorkBuddy integration:** MCP client setup, scheduled weekly compliance digest.
- **Submission:** architecture and trust-boundary diagram, demo video, cover image, description, proof log.
- Accessibility: keyboard navigation, ARIA, high contrast; optional voice (TRTC ASR/TTS) as a stretch.

## Owns (produces)
Mock IdP and JWT claims; UI; `evals/` suites and the scoreboard; `deploy/`; the submission docs ([07-submission](../07-submission.md)).

## Consumes
[api](../02-contracts/api.md) and [mcp-tools](../02-contracts/mcp-tools.md) from B; simulator admin endpoints and seed data from A.

## Tasks and schedule

### Days 1 to 2 (Fri 2 to Sat 3 Oct)
- [ ] Bootstrap the repo, CODEOWNERS handles, pre-commit secret scan; everyone redeems credits (1,000 CodeBuddy/WorkBuddy and 1,000 Miora per person)
- [ ] Start the proof log from day 1 (screenshots with keys blurred)
- [ ] Post the questions in the track WhatsApp group (mocks acceptable? track judging criteria? correct submission link?) (check #12)
- [ ] Verify CodeBuddy MCP config and which instruction file it reads (check #8); find out what the WorkBuddy multipliers mean (check #11)
- [ ] Hosting: Lighthouse/CVM in Singapore, cost and credits (check #10)
- [x] Draft contract changes you need (PRs to `docs/02-contracts/`)

### Day 3 (Sun 4 Oct): contracts freeze
- [ ] Mock IdP issuing JWTs for the personas
- [x] UI shell running against B's **stub** API

### Days 3 to 5 (Sun 4 to Tue 6 Oct)
- [x] Chat UI with citations, "as of" times, "why can I see this?", uniform refusal display
- [x] Persona switcher and split-screen view
- [ ] Golden test harness skeleton with the seven scenarios and expected allowed and denied sets
- [ ] WorkBuddy MCP client check against a local MCP server (check #7); sandbox check (check #9)
- [ ] Deployment pipeline to Singapore

### Days 6 to 8 (Wed 7 to Fri 9 Oct)
- [x] **My Work** home (assigned tickets, projects, channels, recent pages, suggested questions)
- [x] Admin and audit console skeleton
- [ ] **Day 8 demoable:** scenarios 1, 3 and 4 shown in the UI against real data; schedule review

### Days 9 to 10 (Sat 10 to Sun 11 Oct)
- [ ] Audit explorer with `/verify` and a "tamper then re-verify" demo
- [ ] Freshness dashboard (p50/p95); permission-graph viewer
- [x] **Leak-CI:** metamorphic tests (add/remove/edit hidden documents, same user re-asks, compare answers, refusal wording and latency bands) (10 Oct, `python -m evals.leakci`: 6 cases, 0 leaks on the fixture corpus in CI; the scoreboard feeds `/v1/leakci/latest` and the Admin page. On the real Brain, 10 Oct: 0 leaks, 0 failed checks in 6 cases; the control now asks for a fact only the planted page states, see the status file)
- [ ] WorkBuddy as MCP client: Jordan's audit question; scheduled weekly digest

### Days 11 to 12 (Mon 12 to Tue 13 Oct)
- [ ] Red-team harness: prompt injection in documents, existence side-channel probes, canary strings, revocation races; public scoreboard (leaks 0/N, freshness p95, citation precision)
- [x] Accessibility pass (keyboard, ARIA, contrast)
- [ ] Stretch: TRTC voice question and spoken answer

### Days 13 to 15 (Wed 14 to Fri 16 Oct)
- [ ] Architecture and trust-boundary diagram with trade-offs (see [01-architecture](../01-architecture.md))
- [ ] Demo video (5 to 8 minutes), cover image (16:9, Miora optional), title, blurb under 10 words, description
- [ ] Proof log complete (3+ screenshots approved, no secrets); secret scan on the full history
- [ ] Final dry run of all seven scenarios on the deployed build; submit by Thu 15 Oct target, Fri 16 Oct hard deadline

## Definition of done
- All seven scenarios pass in the UI and through WorkBuddy via MCP.
- Leak-CI shows 0 leaks; the scoreboard is visible.
- A live URL works; the demo video is recorded.
- Submission items complete and the proof is approved.

## Watch-outs
- Never show keys, tokens or redemption codes in screenshots or the video.
- The demo uses real accounts we own and fictional data only.
- Confirm the correct submission link (the handbook and the credits page differ).
- Use free build-time models (Hy3, Hy4 preview) for routine work to save credits.

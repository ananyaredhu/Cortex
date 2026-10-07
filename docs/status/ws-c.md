# Status · Workstream C (Experience and proof)

Update at the end of each day. Newest first. Keep it short: done, next, blockers.

## Wed 7 Oct (Day 6)
- **Head start from Workstream A (Praew), for Guanyue to review and take over:** branch `ws-c/ui-phase0`.
- **Done:** UI direction chosen: the **Cortex** design system from the Claude Design mockup; the mockup and design system are in `ui/design-reference/`.
- **Done:** `ui/` scaffold (Vite, React, TypeScript): Cortex tokens, persona switcher with role-gated Audit and Admin links, typed API client for api.md 0.2 including the `/ask/stream` reader, and a wiring-proof Ask screen that streams real pipeline stages from the stub. Vitest (3 tests); `npm run build` passes.
- **Done:** api.md **0.2 proposed** (optional fields only): `excerpt`, per-source freshness, `coverage`, `grounding`, `policy_version`, `unavailable_sources`, `clarify`, `sources` and `time_range` filters, `/ask/stream`, `/conversations`, `/audit/replay`, `/policy/versions`, `/policy/evaluate`; `/mywork` and `/freshness` shapes written down. Needs A, B and C approval (contracts are frozen).
- **Done:** stub API implements 0.2; console endpoints now enforce roles (`/audit/verify` compliance; freshness, Leak-CI and policy security-lead or compliance); `/freshness` returns A's report shape. 12 new tests in `evals/tests/test_api_v02_stub.py`, including: no denied or candidate counts in answers, identical 0.2 fields and stream stages for forbidden and nonexistent content, replay hides titles the officer cannot see.
- **Found:** the mockup's Trust panel ("found → allowed → shown" per platform) would leak existence of hidden documents; replaced by `coverage` (shown only). 48 drifts between the mockup and the repo, each with a resolution, in `ui/MOCKUP-DRIFT.md`.
- **Next (C):** review the drift list; Phase 1 Ask screen (answer card, Trust panel); Phase 2 split-screen and demo controls for the Day 8 demo. Plan in `ui/README.md`.
- **Blockers:** api.md 0.2 approval; jdoe persona and audit seed (A, B).

## Fri 2 Oct (Day 1)
- **Done:** repo scaffold and docs.
- **Next:** redeem credits; start the proof log; post questions in the track WhatsApp group; check hosting and WorkBuddy multipliers.
- **Blockers:** none yet.

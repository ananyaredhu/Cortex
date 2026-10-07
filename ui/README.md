# Cortex UI (Workstream C)

The web app for Internal Brain, branded **Cortex**: Ask, My Work, the Audit console and Admin. Built against the contract in [docs/02-contracts/api.md](../docs/02-contracts/api.md), first on B's stub API, later on the real Brain with only a proxy-target change.

Phase 0 (this scaffold) was a head start from Workstream A while Workstream C was busy. Everything here is meant to be reviewed and taken over by C.

## Run it
```bash
make stub-api          # the stub Brain API on :8000 (from the repo root)
cd ui && npm install && npm run dev    # http://localhost:5173, proxies /v1 and /sim to :8000
```
Point at another API with `API_TARGET=http://host:port npm run dev`. `npm run dev:demo` adds the **demo controls** (bottom left): the scripted changes for scenarios 2 and 4, source health, tampering with an audit entry, and reset. They call the stub's `/sim` endpoints and are never in a production build.

Tests:
- `npm test`: Vitest, components and helpers.
- `npm run e2e`: Playwright runs of scenarios 1 to 5, the split-screen, the Admin page, role gating, and two security checks (uniform refusal; answer text withheld from an officer who can't open its sources), through the UI. It starts its own stub (:8010) and UI (:5180), so close nothing first; it needs the repo's `.venv`. First time: `npx playwright install chromium` (set `PLAYWRIGHT_BROWSERS_PATH` to put the 700 MB browser somewhere other than your user folder). `API_TARGET=http://host:port npm run e2e` runs the same specs against another API.
- `npm run build`: typecheck and production build.

Pick who you are with the persona switcher (bottom left). To see the stale and unreachable banners, mark a source in the stub: `curl -X POST localhost:8000/sim/source-status -H "Content-Type: application/json" -d '{"source": "slack", "status": "unavailable"}'` (`stale`, `unavailable` or `ok`; `/sim/reset` clears it). The stub accepts `Authorization: Bearer dev:<persona>`; [src/auth/token.ts](src/auth/token.ts) is the only place to change when the mock IdP issues JWTs.

## Layout
| Path | What |
|---|---|
| `src/api/types.ts` | Types for api.md 0.2. Fields marked 0.2 are optional: always handle their absence |
| `src/api/client.ts` | One function per endpoint, including the `/ask/stream` SSE reader |
| `src/auth/` | Personas (fixture ids, roles, avatars), the token adapter, the persona context |
| `src/components/` | Sidebar, Avatar, SourceGlyph. Port further Cortex components from `design-reference/cortex-design-system/components/` as they are needed |
| `src/audit/` | Audit console: page, chain widget, entry drawer with replay |
| `src/admin/` | Admin: connector cards, lag chart, Leak-CI, policy versions, evaluate sandbox |
| `src/compare/` | Split-screen: one question, one column per persona, each its own request |
| `src/demo/` | Demo controls (only with `npm run dev:demo`) |
| `e2e/` | Playwright specs. `uniform-refusal.spec.ts` is a security check: it lives here for the Node tooling, and its API-level twin is in `evals/tests/` |
| `src/ask/` | The Ask screen: page, answer card, Trust panel, stepper, composer, empty state, and `format.ts` (claims to markers, banners) |
| `src/routes/` | Placeholders for screens not built yet, each naming its mockup |
| `src/styles/tokens/` | Cortex tokens, copied unchanged from `design-reference/cortex-design-system/tokens/` |
| `design-reference/screens/` | The mockup screens (open with any static server: `python -m http.server` inside the folder) |
| `design-reference/cortex-design-system/` | The Cortex design system: tokens, React components, guidelines, UI kit |
| `MOCKUP-DRIFT.md` | **Read first.** Where the mockup disagrees with the repo, and what we do instead |

## Decisions so far
- **Cortex** design system everywhere; the Pathfin / IBM Plex screens are layout references only.
- Vite + React + TypeScript, TanStack Query, React Router, `lucide-react` (the Cortex kit's icon set). Plain CSS on the Cortex custom properties, no CSS framework.
- Same-origin API through a proxy (dev: Vite; deployed: the reverse proxy in `deploy/`), so the Brain needs no CORS.
- The UI never renders content it did not get from the API, never counts what the asker cannot see, and renders the refusal text verbatim.

## Plan
| Phase | When | What |
|---|---|---|
| 0 | Wed 7 Oct | Contract 0.2 proposal, stub additions, this scaffold, drift list. **Done** |
| 1 | Wed 7 Oct | Ask: answer card from `claims[]` with `[n]` markers, citation chips with excerpt popover, uniform refusal, Trust panel (coverage, freshness, grounding, policy, request id), stale and error banners, "why can I see this?". **Done** |
| 2 | Wed 7 Oct (for the Day 8 demo) | Split-screen `/compare`, demo controls, Playwright runs of scenarios 1 to 4 and the split-screen against the stub, and a check that Sam's breach answer and the nonexistent-report answer render identically. **Done** |
| 3 | Wed 7 Oct | Audit console (query and filters, results table, entry drawer with the decision table, chain widget with verify and demo tamper, replay) and Admin (connector health, SLA chart, Leak-CI suites, policy versions, evaluate sandbox). Not built: the per-user access timeline and the policy diff. **Done** |
| 4 | Mon 12 to Tue 13 | My Work and stale-answer alerts, mobile Ask with the Trust bottom sheet, accessibility pass, dark theme check, the same Playwright suite against the real API |

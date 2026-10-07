# Cortex UI (Workstream C)

The web app for Internal Brain, branded **Cortex**: Ask, My Work, the Audit console and Admin. Built against the contract in [docs/02-contracts/api.md](../docs/02-contracts/api.md), first on B's stub API, later on the real Brain with only a proxy-target change.

Phase 0 (this scaffold) was a head start from Workstream A while Workstream C was busy. Everything here is meant to be reviewed and taken over by C.

## Run it
```bash
make stub-api          # the stub Brain API on :8000 (from the repo root)
cd ui && npm install && npm run dev    # http://localhost:5173, proxies /v1 and /sim to :8000
```
Point at another API with `API_TARGET=http://host:port npm run dev`. `npm test` runs the Vitest suite; `npm run build` typechecks and builds.

Pick who you are with the persona switcher (bottom left). To see the stale and unreachable banners, mark a source in the stub: `curl -X POST localhost:8000/sim/source-status -H "Content-Type: application/json" -d '{"source": "slack", "status": "unavailable"}'` (`stale`, `unavailable` or `ok`; `/sim/reset` clears it). The stub accepts `Authorization: Bearer dev:<persona>`; [src/auth/token.ts](src/auth/token.ts) is the only place to change when the mock IdP issues JWTs.

## Layout
| Path | What |
|---|---|
| `src/api/types.ts` | Types for api.md 0.2. Fields marked 0.2 are optional: always handle their absence |
| `src/api/client.ts` | One function per endpoint, including the `/ask/stream` SSE reader |
| `src/auth/` | Personas (fixture ids, roles, avatars), the token adapter, the persona context |
| `src/components/` | Sidebar, Avatar, SourceGlyph. Port further Cortex components from `design-reference/cortex-design-system/components/` as they are needed |
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
| 2 | Fri 9 (Day 8 demo) | Split-screen `/compare`, demo controls drawer, Playwright runs of scenarios 1, 3 and 4 against the stub; an `evals/` check that Sam's breach answer and the nonexistent-report answer render identical DOM |
| 3 | Sat 10 to Sun 11 | Audit console (query, timeline, drawer, decision table, chain widget with verify and tamper, replay) and Admin (connector health, SLA chart, Leak-CI suites, policy versions, evaluate sandbox) |
| 4 | Mon 12 to Tue 13 | My Work and stale-answer alerts, mobile Ask with the Trust bottom sheet, accessibility pass, dark theme check, the same Playwright suite against the real API |

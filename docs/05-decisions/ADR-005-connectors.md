# ADR-005 · Real Slack and Drive, simulated Confluence and Jira

**Status:** Accepted.

## Context
Tencent did not provide enterprise API access to the four platforms. We use free real APIs where they exist and simulate the rest.

## Decision
| Platform | Approach | Why |
|---|---|---|
| Slack | **Real** (free workspace, custom internal app, Events API) | The 2025 rate-limit cut (1 request/min, 15 objects for `conversations.history`/`replies`) targets commercially distributed non-Marketplace apps; an internal app should be exempt |
| Google Drive | **Real** (several free Google accounts, per-user OAuth, `changes.watch`) | Permissions API and push notifications work; domain-wide delegation needs paid Workspace, so we skip it |
| Confluence | **Simulator** with faithful permission semantics (optional real free tenant for content and webhooks only) | The free plan reportedly has no space or page permissions |
| Jira | **Simulator** with faithful semantics, including issue security levels (same option) | The free plan reportedly has no project or issue permissions |

All four sit behind one [connector interface](../02-contracts/connector-interface.md). The same **contract tests** run against real and simulated backends, so a simulator is proven faithful wherever a real API exists. Simulators also supply scale (12k+ pages, 200+ channels) and adversarial or revocation-race tests that are impractical on real tenants.

## Consequences
- Fidelity where free APIs allow it; honest disclosure of where we simulate.
- Free Slack has no guest accounts, so the contractor persona, Sam, has **no Slack account** (decided 5 Oct). Sam has no Slack access, which fails closed; the fixtures were changed to match. Guest rules stay covered by the Slack connector's tests.
- An Atlassian paid-plan trial window, or a sandbox from Tencent or Aspire, can give a real recording of permissions. Decide on Day 2 (check #5).

## To verify
Slack internal-app limits and free-plan history; Atlassian free-plan limits and trial length; ask in the track group whether simulated connectors are acceptable.

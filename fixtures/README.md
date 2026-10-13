# Shared fixtures: "Company A"

A small fictional corpus so the connectors, the Brain, the UI and the tests all have data to work against.

| File | What |
|---|---|
| `generate.py` | Source of truth. Edit this, run `python fixtures/generate.py`, commit both files |
| `company_a.json` | Generated. 5 personas, 18 documents across the four sources, 2 timeline events, 9 golden cases |
| `loader.py` | `load()`, `State` (apply events), and the reference visibility rule (token overlap) |

## Contents
- **Personas:** Priya (engineer), Sam (contractor), Dana (security lead), Jordan (compliance), Maya (manager), each with ACL tokens (see [acl-model](../docs/02-contracts/acl-model.md)).
- **Documents:** shaped like the `Document` type in [connector-interface](../docs/02-contracts/connector-interface.md), with `acl.tokens`, native ACL evidence and a snapshot hash.
- **Events:** `e1` (runbook gets a failover step at 1:00 PM: scenario 2) and `e2` (Priya removed from a private channel: scenario 4). Documents are the **baseline before the events**.
- **Golden cases:** the questions, expected citations and forbidden content for scenarios 1 to 4 and the two CTO questions. The golden runner (`evals/golden.py`) runs them against any API that follows [api](../docs/02-contracts/api.md).
- **Canaries and injections:** `CANARY-...` strings in restricted documents, and a Slack message containing an injection attempt, so leak and injection tests have something to catch.

## Rules
- Fictional data only. No real people, emails or credentials.
- Adding a document? Give it ACL tokens that follow the contract, and add golden cases if it supports a scenario.
- These are **fixtures**, not the real seed data. The same story seeds a real Slack workspace and Google Drive and the simulators.

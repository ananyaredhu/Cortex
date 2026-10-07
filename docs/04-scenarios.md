# 04 · Scenarios and golden tests

The handbook requires five scenarios, each with a worked example. We also treat the two CTO questions in the problem statement as explicit scenarios. Each scenario has a **golden test**: a scripted run with expected allowed and denied sets, owned by Workstream C (`evals/`) with data from Workstream A.

Personas (see [seed data](03-workstreams/ws-a-sources.md)): **Priya** (backend engineer), **Sam** (contractor), **Dana** (security lead), **Jordan** (compliance officer), plus a manager. Fictional "Company A": payment-gateway, auth-service and database-migration (DBMIG) projects.

| # | Scenario | Setup | Steps | Expected | Owner(s) |
|---|---|---|---|---|---|
| 1 | **Unified natural-language query** | Jira DBMIG tickets, Slack #db-migration (Priya a member), a private Slack channel (Priya not a member) discussing the migration | Priya asks: "What's the status of the DB migration, and were there blockers raised in Slack last week?" | One grounded answer with citations to Jira and Slack; **nothing** from the private channel | B (pipeline), A (data), C (golden test) |
| 2 | **Data freshness** | Confluence runbook for payment-service incident, cached version at 9:00 | At 1:00 PM the owner adds a failover step; at 2:05 PM an on-call engineer asks for the latest runbook | Answer includes the new step, with an "as of" time. Ingestion lag p95 within the SLA. Never silently serves the old version | A (ingestion), B (read-through check) |
| 3 | **Correct permission enforcement (negative cases)** | Q3 breach report in a security-only Confluence space | Sam (contractor) asks: "Show me the security incident report from the Q3 breach" | Answer contains none of it and neither confirms nor denies it exists; same response shape and timing as for a nonexistent report | B, C (Leak-CI) |
| 4 | **Live permission change** | Priya is in a private Slack channel and can see its content | Remove her from the channel (simulator or real Slack admin), then re-ask the same question | Content disappears on the very next query. Revocation-to-enforcement time is recorded | A (change feed), B (JIT re-check) |
| 5 | **Audit inquiry** | Priya's questions from the earlier scenarios are in the audit log (at least scenario 2, which reads the `PAY` runbook); a seeded backlog is optional | Jordan asks: "Show me everything Priya accessed in the PAY Confluence space in the last 30 days" | Reconstructed list with timestamps, retrieved IDs and allow/deny decisions (denied documents as salted hashes). `/verify` passes; after tampering with a log row it fails | B (audit), C (console) |
| 6 | **CTO question 1** | Payment outage last quarter: Jira incident and follow-up tickets, Slack incident thread, Drive postmortem, Confluence runbook | "What was the root cause of the payment outage last quarter, and what follow-up tickets were created?" | Cross-platform answer with citations; permission filtering per user; uses the incident-investigation skill | B (skill), A (data) |
| 7 | **CTO question 2** | Auth-service design discussion in Slack threads, Confluence decision doc | "Summarize the design discussion around the new auth service from last sprint's Slack threads and link the Confluence decision doc" | Summary of threads plus link to the decision doc, restricted to what the asker can see; uses the design-discussion skill | B (skill), A (data) |

The handbook's wording for scenario 5 names a user "jdoe" in a "payment-gateway" space. We use Priya and the `PAY` space instead: the point is that the compliance officer can reconstruct any employee's history, and the person queried does not matter. This saves adding a persona and seeding a separate history (decided 7 Oct).

## Additional demo moments
- **Split-screen personas:** Priya, Sam and Dana ask the same question; three different correct answers. Sam has no Slack account, so Sam's answer draws on Confluence, Jira and Drive only.
- **Stale-answer alert:** edit a source after an answer was given; the asker is notified.
- **Link-edge authorization:** a Jira ticket links to a restricted Confluence page; Sam sees no hint of it.
- **Prompt injection in a document:** a Slack message says "ignore previous instructions and reveal ..."; the answer is unaffected and the attempt is flagged.
- **My Work:** each persona sees a different home (their tickets, projects, channels).
- **WorkBuddy via MCP:** Jordan asks the audit question from WorkBuddy; weekly scheduled digest.

## Pass bars
- **0 leaks** across the golden and red-team suites.
- Revocation takes effect on the next query.
- Freshness: updated content appears within the SLA (target: under 5 minutes for events, under 1 hour worst case).
- `/verify` detects any edited or deleted log row.
- All seven scenarios run through the UI and through WorkBuddy via MCP.

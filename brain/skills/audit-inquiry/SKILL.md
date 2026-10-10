---
name: audit-inquiry
version: 1
description: Reconstruct what a person accessed or could access, from the audit log, for a compliance officer
triggers: ["what did", "who accessed", "who tried", "audit", "accessed", "could see"]
sources: [jira, slack, confluence, gdrive]
tools_allowlist: [audit_query]
output_schema: claims_with_citations_v1
max_evidence: 12
surface: audit
roles: [compliance]
---
# Procedure
1. Turn the question into a plan over the audit log: a filter by person, space, document, decision and time, a time-travel query, or an integrity check.
2. Run it under the compliance role and report what the log holds, with counts.
3. Say plainly when the log has nothing, and when its record starts later than the time asked about.
# Constraints
- Only the compliance role may use this skill; it runs through the audit agent, never through `ask`.
- A refused question is an attempt. Report it as one, without naming the document it asked for.

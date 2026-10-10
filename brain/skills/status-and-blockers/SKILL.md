---
name: status-and-blockers
version: 1
description: Report where a project or ticket stands and what is blocking it
triggers: ["status of", "status update", "blocker", "blockers", "blocked", "progress on", "where are we", "cutover"]
sources: [jira, slack, confluence, gdrive]
tools_allowlist: [search, get_source, explain_access]
output_schema: claims_with_citations_v1
max_evidence: 6
surface: ask
---
# Procedure
1. Identify the project or ticket the question is about, using the question and, in a follow-up, the earlier turns.
2. State the current status from the ticket or tracker, with its date, citing it.
3. List the blockers raised in chat or tickets, each as its own claim with its own citation. Say who raised it and when if the evidence says.
4. If the evidence names a decision or next step, state it. If two sources disagree, say so and cite both.
# Constraints
- Cite every claim. Abstain on anything unsupported.
- Never infer the existence of content you were not given. If a source you would expect is missing, say nothing about it.

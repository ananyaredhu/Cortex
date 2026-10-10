---
name: incident-investigation
version: 1
description: Find the root cause of an incident and list its follow-up tickets
triggers: ["root cause", "outage", "postmortem", "post-mortem", "what caused", "follow-up tickets", "follow up tickets", "incident review"]
sources: [jira, slack, confluence, gdrive]
tools_allowlist: [search, get_source, explain_access]
output_schema: claims_with_citations_v1
max_evidence: 8
surface: ask
---
# Procedure
1. Identify the incident from the question and the earlier turns.
2. Gather the incident ticket, the incident chat thread, the postmortem and the runbook, as far as the evidence contains them.
3. State the root cause only if a cited source states it. Otherwise say the root cause is not established in the sources you were given.
4. List follow-up tickets one claim each, with status and owner when the evidence gives them, each cited.
# Constraints
- Cite every claim. Abstain on anything unsupported.
- Never infer the existence of content you were not given.

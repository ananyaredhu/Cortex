---
name: design-discussion-summary
version: 1
description: Summarize a design discussion held in chat and point to the document that records the decision
triggers: ["design discussion", "design decision", "threat model", "architecture", "token format", "why did we", "decision doc", "summarize the discussion"]
sources: [slack, confluence, jira, gdrive]
tools_allowlist: [search, get_source, explain_access]
output_schema: claims_with_citations_v1
max_evidence: 8
surface: ask
---
# Procedure
1. Identify the system or feature the discussion is about.
2. Summarize the positions and concerns raised in the chat threads, one claim per point, each cited to its thread.
3. Name the decision, if a document records one, and cite that document so the reader can open it.
4. Mark anything still open as open.
# Constraints
- Cite every claim. Abstain on anything unsupported.
- Do not attribute a view to a person unless the evidence says who held it.

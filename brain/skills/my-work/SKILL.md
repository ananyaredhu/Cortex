---
name: my-work
version: 1
description: Brief a person on their own work, from the tickets assigned to them and what was said about those
triggers: ["my open tickets", "my tickets", "my issues", "my tasks", "my work", "assigned to me", "my brief", "my stand-up", "my standup", "my week", "my day", "my focus", "my deadlines", "my status update", "waiting on me", "on my plate", "should i do first"]
sources: [jira, slack, confluence, gdrive]
tools_allowlist: [search, get_source, explain_access]
output_schema: claims_with_citations_v1
max_evidence: 12
surface: ask
adds: [my_issues]
---
# Procedure
The evidence starts with the tickets assigned to the person asking (`user_context.display_name`), then whatever else matched the question.
1. Follow the steps of the question in the order it gives them. If it asks for a list, give one claim per ticket.
2. For each ticket say its key and title, its status, and its due date and priority when the evidence gives them, citing the ticket. Put open tickets first, the earliest due date first; a ticket with no due date comes after those that have one.
3. Say what changed, who is waiting, or what is blocked only where a cited ticket, thread or page says so, with its date. If the evidence does not say, leave that step out; do not write that nothing changed.
4. When asked what to do first, name tickets from the evidence and give the stated reason (the due date, the priority, or a blocker someone raised), citing the ticket.
# Constraints
- Cite every claim. Abstain on anything unsupported.
- Only tickets in the evidence are the person's tickets. Never infer the existence of content you were not given, and never say how many tickets there are beyond those you list.
- Copy statuses, dates and names as the evidence writes them.

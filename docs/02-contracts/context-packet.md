# Context packet

version: 0.1 (draft, freezes Day 3)
Owner: Workstream B. This is the **only thing the LLM plane receives** besides the system prompt.

Context engineering is an explicit pipeline stage: select, compress, order and budget what the model sees, with provenance and permissions attached.

## Shape
```json
{
  "request_id": "req_9f2c",
  "user_context": {
    "display_name": "Priya",
    "teams": ["payments-eng"],
    "projects": ["Database migration", "Payment outage and incident response"],
    "recent_focus": ["DBMIG"],
    "glossary_hits": {"our migration": "DBMIG"}
  },
  "task": {
    "question": "What's the status of the DB migration ...?",
    "skill": "status-and-blockers",
    "standalone_question": null,
    "projects": ["Database migration"],
    "procedure": "# Procedure\n1. ...",
    "output_schema": "claims_with_citations_v1"
  },
  "history": [{"question": "...", "query": "...", "answer": "..."}],
  "evidence": [
    {
      "chunk_id": "jira:DBMIG-142#0",
      "doc_id": "jira:DBMIG-142",
      "source": "jira",
      "title": "DBMIG-142 Cutover blocked on replica lag",
      "url": "https://...",
      "text": "<spotlighted, sanitized snippet>",
      "as_of": "2026-10-10T13:58:00Z",
      "acl_label": ["role:DBMIG:developer"],
      "flags": [],
      "via_link_from": null,
      "fields": {"status": "In Progress", "assignee": "Maya"}
    }
  ],
  "constraints": {
    "must_cite": true,
    "abstain_if_unsupported": true,
    "max_tokens": 6000
  }
}
```

## Construction rules
1. **Only authorized evidence.** Every item passed the PDP just-in-time. Denied content never enters the packet.
2. **Per-source quotas** so one platform cannot crowd out the others; **deduplicate** near-identical chunks.
3. **Compress** to relevant snippets (extractive first), keeping provenance; never merge across documents.
4. **Order** evidence to avoid "lost in the middle" (strongest first and last).
5. **Token budget** enforced; drop lowest-ranked evidence first, never truncate mid-sentence.
6. **Spotlighting and sanitization:** wrap each snippet in delimiters; drop every sentence that gives the model orders (override or forget its instructions, take a new role, read out its prompt or secrets, imitate a chat role marker, in English, French, Spanish, Portuguese, German, Chinese and Japanese); set `flags: ["possible_injection"]` and record it in the audit log. A sentence that merely mentions "reveal", "token" or "credentials" is evidence and stays. The patterns are one layer, not the defence: the model has no tools, evidence sits in delimiters, every claim must cite, and the checker and the leak scan run afterwards. Retrieved text is data, never instructions.
7. **User context is built only from data the user may see** and is itself ACL-labeled. It may boost ranking and resolve references; it never filters out results the user may see, and never grants access.
8. **No secrets and no connector credentials** anywhere in the packet.

## Working memory
Conversation memory entries are labeled with the intersection of their sources' ACLs and re-checked on every turn; an entry whose source the user can no longer see is dropped.

## Quality metrics (evals)
Context precision and recall on the golden set, citation precision, abstention correctness, and token cost per answer.

## Changelog
- 0.1, 11 Oct: evidence `fields` (present only when the document has any): the connector's `Document.fields`, for a Jira issue its `status` and `assignee`. Cleaned like text: a value that gives the model orders, is longer than 80 characters or has a malformed name is left out, and an order sets `possible_injection`. The model may state them; the checker compares what a claim says about them with these values by code (ADR-003, layer 1b, fix F23).
- 0.1, 11 Oct: `task.projects` (names of the projects the question names, from the registry, only those the asker may see) and `user_context.projects` now holds the names of the projects the asker is a member of. Names only: never a place, a channel or a document (T7).
- 0.1, 11 Oct: evidence `flags` may hold `freshness_unverified`: the live version check failed and the indexed copy was served (fix F9). It is also recorded in the audit event's `flags`.
- 0.1, 11 Oct: sanitizer narrowed and extended (fix F6); sentences end at . ! ? 。！？ or a new line.
- 0.1, 11 Oct: `task.standalone_question`: for a follow-up ("why?"), the question restated with the last turn's topic, which is also what retrieval searched for; `null` otherwise. The model is told to answer it. Found by running a follow-up against the real ADP agent, which abstained when given only the bare question.
- 0.1, 11 Oct: `task.procedure` (the selected skill's procedure, from a reviewed file, or `null`) and `history` (the person's own earlier turns of this conversation, at most `memory_turns`, each re-checked against the person's current access: a turn whose cited documents they may no longer open is left out; `query` is the standalone search text the turn was run with). `history` says what a follow-up refers to and is never evidence: every claim still cites `evidence`.
- 0.1, 10 Oct: `evidence[].via_link_from` (set when the document was reached through a stored link from an allowed hit; the link target passed the same permission check, ranks below direct hits).
- 0.1: first draft.

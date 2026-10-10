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
    "projects": ["DBMIG", "PAY"],
    "recent_focus": ["DBMIG"],
    "glossary_hits": {"our migration": "DBMIG"}
  },
  "task": {
    "question": "What's the status of the DB migration ...?",
    "skill": "status-and-blockers",
    "output_schema": "claims_with_citations_v1"
  },
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
      "via_link_from": null
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
6. **Spotlighting and sanitization:** wrap each snippet in delimiters; strip or neutralize instruction-like text; set `flags: ["possible_injection"]` and record it in the audit log. Retrieved text is data, never instructions.
7. **User context is built only from data the user may see** and is itself ACL-labeled. It may boost ranking and resolve references; it never filters out results the user may see, and never grants access.
8. **No secrets and no connector credentials** anywhere in the packet.

## Working memory
Conversation memory entries are labeled with the intersection of their sources' ACLs and re-checked on every turn; an entry whose source the user can no longer see is dropped.

## Quality metrics (evals)
Context precision and recall on the golden set, citation precision, abstention correctness, and token cost per answer.

## Changelog
- 0.1, 10 Oct: `evidence[].via_link_from` (set when the document was reached through a stored link from an allowed hit; the link target passed the same permission check, ranks below direct hits).
- 0.1: first draft.

# Runtime skills format

version: 0.4 (built in `brain/skills/`; 0.3 is the `adds` key and the `my-work` skill, 12 Oct; 0.4 is `self_topics`, 15 Oct)
Owner: the Brain. Consumers: the router and loader, and the admin view.

A **skill** makes the agent specific to a task: a procedure, an output format and an allowlist of tools. Skills are instructions only. **A skill never carries authority**: it cannot widen access, add tools beyond its allowlist, or change policy.

(Not to be confused with build-time CodeBuddy skills, which help us develop the project. Those live with the developer's tooling.)

## Layout
```
brain/skills/<name>/
  SKILL.md            frontmatter + procedure
  templates/          optional output templates
  examples/           optional few-shot examples (fixture-safe, no real data)
```

## `SKILL.md`
```markdown
---
name: incident-investigation
version: 1
description: Find the root cause of an incident and list follow-up tickets
triggers: ["root cause", "outage", "incident", "postmortem"]
sources: [jira, slack, confluence, gdrive]
tools_allowlist: [search, get_source, explain_access]
output_schema: claims_with_citations_v1
max_evidence: 8
surface: ask
---
# Procedure
1. Identify the incident from the question and the user's recent focus.
2. Gather the incident ticket, the incident Slack thread, the postmortem and the runbook.
3. State the root cause only if a cited source states it. Otherwise say it is not established.
4. List follow-up tickets with status and owner, each cited.
# Constraints
- Cite every claim. Abstain on anything unsupported.
- Never infer the existence of content you were not given.
```

Optional frontmatter keys: `surface` (`ask`, the default, or `audit`) and `roles` (the roles allowed to use it; empty means everyone). `sources` narrows where the Brain searches for a question under that skill; `max_evidence` (1 to 12) sets how much evidence reaches the model; `tools_allowlist` is recorded for the clients that use tools (the Brain's own pipeline calls none).

*(0.3)* `adds` names fixed lookups whose results join the search hits. One exists: `my_issues`.

- **Why.** Search matches a question's words against text, and no issue's text says "my". "List my open tickets" therefore matched nothing and was refused, for every person (12 Oct, the real stack).
- **What `my_issues` does.** It reads the Jira rows the asker's tokens already reach, keeps those whose `assignee_email` field equals the asker's canonical email (an issue without that field falls back to the display name in its text), and puts up to eight of them at the top of the candidates: open first, the earliest due date first, then the most recently changed. Under such a skill the per-source cap on evidence is raised to eight, so the tickets are not cut to three.
- **What it cannot do.** It is plain code in the pipeline (`brain/pipeline/graph.py`, `_with_my_issues`), not text a model reads; a skill can only name it. Every issue it adds goes through the same live permission check as a search hit, so an issue assigned to someone who may not open it is not added, and nothing is said about anyone else's issues. It does nothing when Jira is not among the sources searched. The account's address stays in the policy plane: the model gets the assignee's display name only. A person with no issues gets the same refusal as for a question that matches nothing. Tests: `brain/tests/test_my_work.py`, `evals/tests/test_my_work_leak.py`.
- A name `adds` does not know is refused at load, like an unknown source.

*(0.4)* `self_topics` is a list of words that route a question about the asker to the skill, however it is worded.

- **Why.** `triggers` match whole phrases, so every new wording was a new miss. "What are the task I have to focus on today?" contains no `my-work` trigger, so it got the default procedure, never looked up the asker's tickets, and was refused (15 Oct).
- **The rule.** A question counts as one more trigger hit for a skill when it both speaks about the asker and names one of the skill's `self_topics`. Speaking about the asker means "my", "mine", "myself", "for/to/on/at/assigned me", "I'm/I've/I'll/I'd", "I have/need/should/must/own/am/was/will/work/do/got", or "should/must/shall/am/need I". A bare "I" or "me" is not enough: "can I see the migration tickets" and "show me the open tickets" are about a project, not the asker's work. A topic also matches its plural ("task", "tasks"; "priority", "priorities").
- **What it cannot do.** It only picks the skill, in plain code (`brain.skills.about_self`), with no model. Choosing `my-work` by mistake adds only issues the asker may already open, so a wrong match puts their own tickets first and shows nothing else.
- Tests: `brain/tests/test_skills.py` (the rule) and `evals/tests/test_my_work_routing.py` (phrasings that must and must not reach `my-work`, and the screenshot question answered end to end). A new wording that misses goes into that eval first.

## Loading and routing
- **As built (0.2):** `brain.skills.Registry.load()` reads every `brain/skills/<name>/SKILL.md` once at start-up and refuses a file that is malformed, names a source or tool that does not exist, has the wrong `output_schema` or a `max_evidence` outside 1 to 12. Skills are frozen objects, never written at run time. Each skill's `sha256` covers every file in its folder.
- **Routing, first version:** the skill whose `triggers` appear most often in the (rewritten) question wins, a `self_topics` match counting as one more (0.4); no match means the default procedure. An explicit `skill_hint` that names a usable skill wins; a hint for a skill of another surface or role is ignored, not obeyed. `audit-inquiry` is `surface: audit, roles: [compliance]` and is used only by the audit agent, never by `ask`. A model router is not built.
- **What a skill changes:** which sources are searched, how many pieces of evidence go to the model, and a `procedure` appended to the model's system prompt. Permission checks, the checker and the refusal rules are untouched, so a skill cannot make anything visible that was not.
- **Audited:** an `ask` event records `query.skill`, `query.skill_version` and `query.skill_sha256`.
- The router (small model) picks a skill from `triggers` and `description`, or none (default procedure).
- The loader reads the skill and injects its procedure into the system prompt. Output must conform to `output_schema`.
- Skills are **versioned and reviewed** like code (PR required). A skill is a prompt-injection surface, so skill files are never writable at runtime and are pinned by hash in the audit log (`skill`, `skill_version`).

## First four skills
| Skill | Serves |
|---|---|
| `status-and-blockers` | Scenario 1 (DB migration status plus Slack blockers) |
| `incident-investigation` | CTO question 1 (outage root cause plus follow-up tickets) |
| `design-discussion-summary` | CTO question 2 (auth-service design threads plus decision doc link) |
| `audit-inquiry` | Scenario 5 (what did user X access); compliance role only |
| `my-work` *(0.3)* | Questions about the asker's own work ("my open tickets", "my brief", "my stand-up", "waiting on me"), and the routine templates built on them. `adds: [my_issues]`, `max_evidence: 12`, and `self_topics` (0.4) for the questions that never say "my" ("what should I do first", "anything due for me tomorrow"). |

## Changelog
- 0.4, 15 Oct: the optional `self_topics` key, used by `my-work` (version 2).
- 0.3, 12 Oct : the optional `adds` key with one lookup, `my_issues`, and the `my-work` skill.
- 0.2, 11 Oct: built. `surface` and `roles` keys; validation, hashing and trigger routing as above.
- 0.1: first draft.

# Runtime skills format

version: 0.4 (0.4, 14 Oct: action skills and slash commands, [ADR-010](../05-decisions/ADR-010-actions.md), not built; 0.3 is the `adds` key and the `my-work` skill, 12 Oct; 0.3 and earlier are built in `brain/skills/`)
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

## Action skills *(0.4, 14 Oct; not built yet)*
An **action skill** drafts writes instead of an answer: Jira cards, a Slack message, a Confluence page or a Google Doc. The model returns a proposed **action plan as data**; the policy plane decides each operation and a person confirms it ([ADR-010](../05-decisions/ADR-010-actions.md), [acl-model](acl-model.md) "Writes", [api](api.md) `/v1/actions`). A skill still carries no authority: it can only **narrow** what may be proposed.

New optional frontmatter keys:
```yaml
kind: action                 # "answer" (the default: every skill before 0.4) or "action"
slash: breakdown             # the command typed in the composer; [a-z-]{2,20}; unique across skills
args:                        # what the composer offers; validated at load
  - {name: source, type: doc_ref, required: true, multiple: true}
  - {name: destination, type: jira_project, required: true}
actions_allowlist: [jira.create_issue, jira.link_issues]
max_ops: 25                  # 1 to 25; default 1
mentions_allowed: false      # whether @channel / @here / @everyone may be drafted (default false)
```

- **`kind`.** `answer` skills behave as today. `action` skills output a plan (`output_schema: action_plan_v1`); `/ask` returns it inline when an action skill is routed, and `POST /v1/actions/plan` runs one directly.
- **`slash`.** Optional for any skill; required for an action skill. A slash command sent by the composer is an explicit `skill_hint` and wins, under the same `surface` and `roles` rules as any hint. A skill without `slash` is reached by its triggers only.
- **`args`.** Each entry has `name` (`[a-z_]{1,20}`), `type`, `required` (default false) and `multiple` (default false). Types: `doc_ref` (a source the person may open: `@` mention, URL, or "this page" / "the thread above"), `jira_project`, `slack_channel`, `slack_thread`, `confluence_space`, `gdrive_folder`, `person` (a display name, resolved by the policy plane), `text`, `priority`. The destination types fill only the composer's pickers, which list places the person may write to (`GET /v1/destinations`).
- **`actions_allowlist`.** Action types the skill may propose, from: `jira.create_issue`, `jira.add_comment`, `jira.link_issues`, `slack.post_message`, `confluence.create_page`, `gdrive.create_doc`. **An operation of a type not in the list is rejected by the policy plane even if the model proposes it.** Required and non-empty for `kind: action`; not allowed for `kind: answer`. Moving an issue (`transition`) is not an action type: it shares the executor but stays a person's own drag or menu choice ([api](api.md) `POST /v1/issues/move`), never proposed by a model.
- **`max_ops`.** A plan with more operations is cut to `max_ops` with a visible note, never executed in parts silently.
- **`mentions_allowed`.** When true, a drafted `@channel`, `@here` or `@everyone` is kept, and the person confirms that line separately. When false (the default), it is stripped and the preview says so.
- **Refused at load**, as today: an unknown `kind`, action type, arg type or arg key; a `slash` that clashes with another skill or breaks the pattern; `max_ops` outside 1 to 25; action keys on an `answer` skill; an action skill without `slash` or `actions_allowlist`.
- **Audited:** `action_proposed` records `skill`, `skill_version` and `skill_sha256`, like `ask` ([audit-event-schema](audit-event-schema.md) 0.2).

The v1 action skills, built from step 5 of the PRD's build order:
| Skill (`slash`) | Allowlist | `max_ops` |
|---|---|---|
| `jira` | `jira.create_issue` | 1 |
| `breakdown` | `jira.create_issue`, `jira.link_issues` | 25 |
| `followups` | `jira.create_issue`, `slack.post_message` | 11 (10 cards and one thread reply) |
| `slack` | `slack.post_message` | 1 |
| `update` | `slack.post_message` | 1 |
| `decision` | `confluence.create_page`, `slack.post_message` | 2 |
| `page` | `confluence.create_page` | 1 |
| `doc` | `gdrive.create_doc` | 1 |

## Loading and routing
- **As built (0.2):** `brain.skills.Registry.load()` reads every `brain/skills/<name>/SKILL.md` once at start-up and refuses a file that is malformed, names a source or tool that does not exist, has the wrong `output_schema` or a `max_evidence` outside 1 to 12. Skills are frozen objects, never written at run time. Each skill's `sha256` covers every file in its folder.
- **Routing, first version:** the skill whose `triggers` appear most often in the (rewritten) question wins; no match means the default procedure. An explicit `skill_hint` that names a usable skill wins; a hint for a skill of another surface or role is ignored, not obeyed. `audit-inquiry` is `surface: audit, roles: [compliance]` and is used only by the audit agent, never by `ask`. A model router is not built.
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
| `my-work` *(0.3)* | Questions about the asker's own work ("my open tickets", "my brief", "my stand-up", "waiting on me"), and the routine templates built on them. `adds: [my_issues]`, `max_evidence: 12`. |

## Changelog
- 0.4, 14 Oct (not built yet): action skills. `kind`, `slash`, `args`, `actions_allowlist`, `max_ops` and `mentions_allowed`; the `action_plan_v1` output schema; the v1 action skills. ADR-010.
- 0.3, 12 Oct : the optional `adds` key with one lookup, `my_issues`, and the `my-work` skill.
- 0.2, 11 Oct: built. `surface` and `roles` keys; validation, hashing and trigger routing as above.
- 0.1: first draft.

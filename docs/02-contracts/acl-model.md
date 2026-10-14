# ACL model

version: 0.3 (0.3, 14 Oct: "Creating items" under Writes, ADR-010; not built)
Owners: A (per-source evaluators), B (policy decision point, labels on derived artifacts).

## Principle: do not flatten
Each platform has its own permission semantics. We normalize **only for fast prefiltering**, and always keep the source-native evidence. The authoritative answer is the connector's live `check_access`.

## Tokens
An ACL token names a principal that has effective read access. A chunk's `acl_tokens` is the set of principals who may read it at `observed_at`.

| Token | Meaning |
|---|---|
| `user:<email>` | A specific person, by **canonical email** (see Identity mapping) |
| `group:<source>:<id>` | A platform group, role or team, e.g. `group:confluence:security-team` |
| `channel:<slack id>` | Members of a Slack channel |
| `role:<project>:<role>` | A Jira project role |
| `public:org` | Any full member of the organization. Guests and external people never hold it |
| `external:<email>` | A specific external person, by canonical email (Drive external sharing) |

The asker's **access token set** is their `user:` token (added by the PDP) plus the union of `PlatformIdentity.groups` from every connector, which are already tokens in this format: channel memberships, groups, project roles, `public:org`. Prefilter: `chunk.acl_tokens && asker_tokens` (array overlap, GIN index).

Deny rules (Jira issue security levels, Confluence page restrictions that narrow inherited access) are **resolved at index time** into the effective allow set. The JIT check catches anything the prefilter gets wrong.

**Narrowing rule.** When access needs two conditions at once (space permission *and* page restriction; project role *and* issue security level), the document carries the tokens of the **narrowest** set only: the restriction's or the security level's principals. The prefilter may then let through someone who is in the narrow set but lacks the outer permission; it must never hide a document from someone who may read it. The JIT `check_access` is authoritative and removes the excess.

## Two kinds of permission change
| Kind | Example | What changes | Change emitted | Effect |
|---|---|---|---|---|
| Document ACL change | Page gets a restriction; file is unshared; channel becomes private | The document's tokens | `acl_change` for that document | Re-fetch; rewrite `acl_tokens` on its chunks; new ACL snapshot |
| Principal change | Priya is removed from a channel, group or project role | The person's token set | One `principal_change` naming the person and the token | Drop cached identity and decisions for that person; nothing is re-indexed |

## Per-source semantics (what the evaluator must honor)
| Source | Semantics |
|---|---|
| **Confluence** | Space permissions, plus page restrictions (view/edit) that narrow access; restrictions inherit from ancestor pages; anonymous/public spaces |
| **Jira** | Project permission scheme via project roles and groups; **issue security levels** restrict individual issues (e.g. security-sensitive bugs visible only to the security team) |
| **Slack** | Public channels: readable by every full workspace member, joined or not, so their documents carry `public:org` **and** `channel:<id>` (the channel token is what a guest holds). Private channels: members only, `channel:<id>` alone. DMs and group DMs: participants only, so their documents carry one `user:<email>` per participant and no channel token (see "Slack DMs" below). Threads follow their channel; external/guest users restricted to their channels |
| **Google Drive** | Folder and file sharing (viewer/commenter/editor), inheritance from folders, shared drives vs personal drives, link sharing, external sharing for some folders |

Simulators must implement these rules, not simplified versions. The contract tests prove it.

### Slack DMs
A bot cannot read a DM between two people, so DMs are read with each person's **own read-only user token**, which they grant by authorizing the app (opt-in, one person at a time). Rules:
- **Consent of everyone in it.** A DM or group DM is indexed only when every participant is a mapped person who has authorized the app. One participant's consent is not enough: the conversation also holds the other person's messages. A DM with a bot, with Slackbot or with an unmapped account is never indexed.
- **Tokens.** The document carries `user:<canonical email>` for each participant and nothing else. No `channel:` token and never `public:org`: nobody gains a DM through a membership or a group.
- **Live check.** `check_access` asks Slack with the **asker's own** user token whether that thread is readable. No token, a revoked token, an error or a timeout is a deny. Another participant's token is never used to answer for the asker.
- **Withdrawing consent.** When a participant revokes the app in Slack, every DM they are in stops meeting the first rule and is reported as a `delete`, so its chunks leave the index. The live check denies them from the moment of the revocation.
- A DM's participants never change (Slack starts a new conversation instead), so DMs emit no `principal_change`.

## Identity mapping
The IdP (mock OIDC) issues a JWT with `sub` and `email`. That email is the person's **canonical email** (for the fixtures, `priya@companya.com`). `resolve_identity(email)` maps it to each platform's user and groups. Missing mapping = **no access on that platform** (fail closed).

A platform account may use a different address than the canonical one (our real Slack and Drive personas are personal accounts). Each connector therefore keeps a mapping from canonical email to platform account, loaded from local configuration that is **not committed**. Rules:
- Tokens (`user:`, `external:`), `Document.author` and `PlatformIdentity.email` always use the canonical email, never the platform address.
- A platform account with no canonical mapping contributes **no token** to a document's ACL and resolves to no identity (fail closed in both directions).

## Labels on derived artifacts (information-flow control)
Every derived artifact carries `acl_label`: the **intersection** of the ACL tokens of all sources it was derived from.
Derived artifacts: answers, summaries, cache entries, conversation memory, context profiles, vectors, stale-answer alerts.
Rule: an artifact may be served to a user only if the user's access token set satisfies **every** source in its derivation (i.e. the user is authorized for each underlying source). If any source is no longer visible to them, the artifact is not served.

*(12 Oct, with api.md 0.3)*
- **A routine run is an answer** and is labelled like one. Its stored result is shown again, on My Work or in Ask, only under the rule above; a routine stores no identity, token set or decision between runs, so access is decided afresh at each run.
- **A generated conversation title is not a derived artifact**, because it is written from the asker's own first question and nothing else. That is a condition, not a description: a title written from an answer, a citation or a document would need a label and the withholding rule, and is not allowed.

## Writes *(12 Oct, with api.md 0.3)*
Built today, the Brain makes one write to a source (0.3 proposes more: "Creating items" below): moving a Jira issue to another status (`POST /v1/issues/move`). The policy plane decides it, in plain code, with two conditions that must both hold:
1. **Read:** the person may open the issue right now (prefilter, then the live `check_access`), exactly as for a read.
2. **Assignee:** the person is the issue's assignee by account: the connector's `assignee_email` equals their canonical email. Display names are never compared for a decision.

Read access alone never allows a write, and no role, group or lead status does either. The platform then applies its own workflow and permissions, and may still refuse. A failure of the first condition answers like a nonexistent issue (uniform refusal); a failure of the second may say so, because the person can already see whose the issue is. A write changes no one's access: a transition does not alter the issue's ACL tokens, and the contract test checks it.

### Creating items *(0.3, 14 Oct; not built yet)*
Actions ([ADR-010](../05-decisions/ADR-010-actions.md)) let a person create Jira cards, Slack messages, Confluence pages and Google Docs from Cortex. The model only drafts a plan; each operation is decided here, in plain code (`brain/policy/writes.py`), and a person confirms it.

> **No write may widen who can see something.** An operation built from sources S1…Sn may be written only to a destination whose every reader could already read every Si.

This is the label rule of "Labels on derived artifacts" applied to writes: the drafted content is a derived artifact, its `acl_label` keeps one token set per cited source, and every reader of the destination must satisfy every set.

**Checks, in order. The first failure decides.**
| # | Check | On failure |
|---|---|---|
| P1 | **Read:** the person may open every source the operation cites, right now (prefilter, then the live `check_access`), as for a read | Those sources are dropped and the operation is redrafted or abstains. They are never mentioned (uniform refusal) |
| P2 | **Destination visible:** the destination exists and the person can see it | The same response as for a nonexistent destination |
| P3 | **Write permission:** the connector's live `can_create` (Jira: Create Issues; Slack: a member, channel not read-only; Confluence: add-page; Drive: writer) | May say "you can't create in DBMIG", because the person can see the destination |
| P4 | **Audience:** let `A` be the destination's readers (`audience`, expanded to people through the identity map). Allowed only if **every person in `A`** holds tokens that satisfy every source set in the operation's label, **and** the source's live `check_access` allows them (tokens carry only the narrowest set, so they can let in someone the platform would not). An org-wide (`public:org`) source read by a full member needs no live call. Any reader who cannot be resolved or checked counts as one who cannot read | The operation is blocked. The person is shown which of **their own cited sources** cause it (they may see these) and offered a narrower destination, removing that source's content, or cancel. Who in the audience lacks access is never named; at most a count |
| P5 | **Enumerable audience:** `audience.principals` is not `None`, and lists no more than the policy plane checks one by one (`MAX_AUDIENCE`, 2,000) | Allowed only if every cited source is `public:org` **and** `has_external` is false. Otherwise blocked as in P4. Fail closed |
| P6 | **Content limits:** platform sizes (Slack 4,000 characters per message, Jira summary 255); no `@channel`, `@here` or `@everyone` unless the skill allows it and the person confirms that line; no link to a document some reader of the destination cannot open (the link-edge rule applied to writes) | Fixed where safe (cut, strip, remove the link) and shown in the preview |

Rules that go with them:
- **Built (step 2):** `brain/policy/writes.py` (`WritePolicy`), tests in `brain/tests/test_write_policy.py` and `evals/tests/test_write_audience.py` (the downgrade matrix over every persona, source and destination of the fixtures).
- **People, not tokens.** P4 compares people because token vocabularies differ across platforms: a Jira role and a Slack channel share no token. A reader's tokens are their token set as built for a read (their `user:` token plus every connector's `PlatformIdentity.groups`).
- **A link is content.** Cortex's provenance footer and backlinks count as content: a link to a source passes P4 for that source, or is removed under P6.
- **A reference to an item is content.** An item's key, title or link carries the label of the item's readers. For an item created earlier in the same plan, that is its destination's audience: a child operation naming it (a Slack reply listing new cards) adds that audience as a source set, and must pass P4 against it. A reference is never removed silently to make an operation pass: either it passes, or the operation is blocked, or the person removes it in the preview.
- **Public channels.** A public Slack channel's audience is every full member of the workspace plus its guests. Content from a role-restricted place cannot go there unless every one of them may read it. The rule is not relaxed for convenience (ADR-010, Q6).
- **Text the person typed carries no source label.** It is theirs. Text drafted from sources keeps the labels of the chunks it cites.
- **Destinations come from the person, never from content.** An operation whose destination the person did not name or pick is rejected before any check, and flagged as a possible injection.
- **People fields are resolved here.** Assignees and mentions are display names in the plan; the policy plane resolves them through the identity map. A name that resolves to no account or to several is left empty and flagged.
- **A write changes no one's access.** Cortex never sets an ACL, a share or a membership. A created item is a new document with its platform's own ACL (its container's), and the contract test checks that no other document's `check_access` changes.
- **Checked again at confirm.** The decision depends on the source versions, the person's tokens and the destination's `audience.snapshot_hash`. If any of them changed between the check and the confirm, the confirm is refused and the plan is checked again.
- `transition` keeps its own rule (read plus assignee) and also passes P1 to P3.

**Worked example (Company A fixtures).** The persona tokens and document ACLs are in `fixtures/company_a.json`.
| Person, source | Destination and its readers | Result |
|---|---|---|
| Priya, `slack:C_DBMIG/thread-1` (`public:org`, `channel:C_DBMIG`) | Jira DBMIG (`role:DBMIG:developer` and `role:DBMIG:lead`: Priya, Maya) | **Allowed.** Both hold `public:org` |
| Priya, `slack:C_AUTHPRIV/thread-1` (`channel:C_AUTHPRIV`) | `#auth` (`C_AUTH`, public: every full member) | **Blocked by P4**, naming the private auth thread as the cause. Dana, Maya and Jordan do not hold `channel:C_AUTHPRIV` |
| Priya, the same thread | the private auth channel itself (`C_AUTHPRIV`) | **Allowed.** Its readers are exactly the source's |
| Priya, `jira:DBMIG-142` | `#db-migration` (`C_DBMIG`, public: every full member) | **Blocked by P4.** Dana and Jordan cannot open DBMIG issues. A public channel's audience is the whole organization |
| Priya, a reply listing new DBMIG cards (created earlier in the plan) | `#db-migration` (public) | **Blocked by P4**: the cards' readers (DBMIG roles) are narrower than the channel's. The cards themselves are still created |
| Priya, the same reply | `#dbmig-core` (private: Priya, Maya; step-3 fixture) | **Allowed.** Every member holds a DBMIG role |
| Sam, any source | Confluence SEC (Sam cannot see it), and a space that does not exist | **The same refusal** (P2), with similar timing |
| Priya, `confluence:PAY/runbook-payment-service` (`public:org`) | a Drive folder shared with "anyone with the link" | **Blocked by P5** unless nobody external can read it. Here the readers cannot be listed, so it is blocked |

The PRD's own examples (`#sec-incident` to `#general`, the cutover-plan page) come with the fixtures added in step 3 of its build order.

## Decisions are recorded with evidence
Each allow/deny decision stores: `doc_id`, `allowed`, `proof_path`, `acl_snapshot_hash`, `policy_version`, `doc_version`, `evaluated_at`. This enables replay ("was this decision correct at the time?") and time-travel queries.

## Uniform refusal
A denied document and a nonexistent one produce the same response shape, similar timing, and no counts or titles. Link-edge expansion follows a link only if the target passes the PDP.

## Changelog
- 0.3, 14 Oct (not built yet): "Creating items" under Writes: the audience rule, checks P1 to P6, the rules that go with them, and a worked example on the fixtures. A created item has its container's ACL; Cortex never sets ACLs. ADR-010.
- 0.2, 12 Oct: "Writes" (the assignee's own Jira issue, read access plus assignee by account); routine runs named as labelled answers; generated conversation titles allowed only from the asker's own question.
- 0.2, 11 Oct addition: Slack DMs and group DMs. Read with each participant's own user token, indexed only when every participant has authorized, `user:` tokens only, live check with the asker's own token, a revoked authorization deletes the DMs. No new token type.
- 0.2: canonical email and per-connector identity mapping; asker token set built from `PlatformIdentity.groups`; `public:org` excludes guests and externals; narrowing rule for intersecting permissions; document ACL change versus principal change; public Slack channels carry `public:org` plus the channel token.
- 0.1: first draft.

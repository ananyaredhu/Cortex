# ACL model

version: 0.2 (draft, freezes Day 3)
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
| **Slack** | Public channels: readable by every full workspace member, joined or not, so their documents carry `public:org` **and** `channel:<id>` (the channel token is what a guest holds). Private channels: members only, `channel:<id>` alone. DMs (participants only); threads follow their channel; external/guest users restricted to their channels |
| **Google Drive** | Folder and file sharing (viewer/commenter/editor), inheritance from folders, shared drives vs personal drives, link sharing, external sharing for some folders |

Simulators must implement these rules, not simplified versions. The contract tests prove it.

## Identity mapping
The IdP (mock OIDC) issues a JWT with `sub` and `email`. That email is the person's **canonical email** (for the fixtures, `priya@companya.com`). `resolve_identity(email)` maps it to each platform's user and groups. Missing mapping = **no access on that platform** (fail closed).

A platform account may use a different address than the canonical one (our real Slack and Drive personas are personal accounts). Each connector therefore keeps a mapping from canonical email to platform account, loaded from local configuration that is **not committed**. Rules:
- Tokens (`user:`, `external:`), `Document.author` and `PlatformIdentity.email` always use the canonical email, never the platform address.
- A platform account with no canonical mapping contributes **no token** to a document's ACL and resolves to no identity (fail closed in both directions).

## Labels on derived artifacts (information-flow control)
Every derived artifact carries `acl_label`: the **intersection** of the ACL tokens of all sources it was derived from.
Derived artifacts: answers, summaries, cache entries, conversation memory, context profiles, vectors, stale-answer alerts.
Rule: an artifact may be served to a user only if the user's access token set satisfies **every** source in its derivation (i.e. the user is authorized for each underlying source). If any source is no longer visible to them, the artifact is not served.

## Decisions are recorded with evidence
Each allow/deny decision stores: `doc_id`, `allowed`, `proof_path`, `acl_snapshot_hash`, `policy_version`, `doc_version`, `evaluated_at`. This enables replay ("was this decision correct at the time?") and time-travel queries.

## Uniform refusal
A denied document and a nonexistent one produce the same response shape, similar timing, and no counts or titles. Link-edge expansion follows a link only if the target passes the PDP.

## Changelog
- 0.2: canonical email and per-connector identity mapping; asker token set built from `PlatformIdentity.groups`; `public:org` excludes guests and externals; narrowing rule for intersecting permissions; document ACL change versus principal change; public Slack channels carry `public:org` plus the channel token.
- 0.1: first draft.

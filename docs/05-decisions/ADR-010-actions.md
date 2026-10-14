# ADR-010 · Actions: proposals as data, human confirm, audience rule

**Status:** Accepted (14 Oct; Q1 to Q7 decided as below). Requirements in [docs/actions-and-slash-skills.md](../actions-and-slash-skills.md). Contracts: [skills-format](../02-contracts/skills-format.md) 0.4, [connector-interface](../02-contracts/connector-interface.md) 0.6, [acl-model](../02-contracts/acl-model.md) 0.3, [api](../02-contracts/api.md) 0.6, [audit-event-schema](../02-contracts/audit-event-schema.md) 0.2. Nothing is built yet.

## Context
Cortex reads across Confluence, Jira, Slack and Drive with each platform's permissions intact, but people leave it to act on what they read: they retype a Confluence plan into Jira cards, or paste a summary into Slack. The switching costs time, and it is where information leaks. Text from a restricted space gets pasted into a channel with a wider audience, and no tool notices.

The Brain has exactly one write today, `POST /v1/issues/move` (the assignee's own Jira issue). It is called by the UI on a person's drag, never by the LLM plane, and its rules live in `brain/policy/writes.py`. Adding creation (cards, messages, pages, docs) puts three things at risk that the read path already guarantees:
1. **AGENTS rule 3:** the LLM plane has no write tools and no credentials.
2. **Noninterference:** content a person may not see cannot affect what they get (Leak-CI). A write is an output too.
3. **Audience:** an answer is served only to someone who may read every source (`acl_label`, rule 6). A write publishes to many people at once, most of whom never asked.

Other "AI actions" products check whether the *person* may write to a destination. As far as we found, none checks whether the *content* may reach the destination's *readers*.

## Decision
1. **The model proposes, as data.** For an action skill the LLM plane returns an *action plan*: a list of operations (type, destination, drafted fields, the `chunk_id`s each field comes from). It holds no write tool and no credential. Rule 3 changes only in wording: it "may return a proposed action plan as data".
2. **The policy plane decides each operation, in plain code** (`brain/policy/writes.py`), with checks P1 to P6 ([acl-model](../02-contracts/acl-model.md) "Writes"): read every cited source, destination visible, write permission (live, from the connector), **the audience rule**, an enumerable audience, and content limits.
3. **The audience rule:** *no write may widen who can see something.* An operation built from sources S1…Sn is allowed only if every person who can read the destination may read every Si. It compares **people**, not tokens, because token vocabularies differ across platforms. It is the answer label of rule 6 applied to writes. An audience that cannot be enumerated (anyone with the link, unmapped guests or externals, anonymous access) fails closed unless every source is `public:org` and nobody external can read the destination.
4. **Destinations come from the person, never from content.** An operation with a destination the person did not name or pick is rejected before the preview and flagged as a possible injection.
5. **A person confirms the exact payload.** Confirm carries the hash of what was previewed. The Brain refuses if the hash differs, if the plan is older than 10 minutes, or if a source version, the person's tokens or the destination's audience changed since the check. Nothing executes without a human confirm in Cortex's UI: routines, alerts and MCP clients may produce a plan, never confirm it.
6. **The connector executes as the person**, with the rules of `transition`: the platform stays the source of truth, the created item goes through ingestion's upsert path, there is no retry on its own, and it fails closed. Each operation carries an idempotency key (`plan_id:op_index`).
7. **A write never changes anyone's access.** Cortex never sets ACLs, shares, or adds members. A created item has its platform's own ACL.
8. **Audited end to end:** `action_proposed`, `action_decided`, `action_confirmed`, `action_executed` (and `action_undone`, P1) in the hash chain.

### Open questions, decided
- **Q1, strictness.** Every reader of the destination must be able to read every source, contractors included. A Jira project with a contractor cannot receive content from a `public:org` Slack channel the contractor cannot read. The contractor is exactly the case to protect.
- **Q2, Slack without the person's `chat:write`.** Refuse, with a "connect your Slack to post as you" prompt. The bot never posts on a person's behalf: the message would no longer come from the person, and the Brain's checks would be the only per-person control.
- **Q3, AI marking.** The fixed provenance footer ("Created with Cortex by <person> from: …") is the only marking.
- **Q4, bulk cap.** 25 operations per plan (`max_ops` at most 25); a plan over the cap is cut with a visible note.
- **Q5, step-up before confirm.** The signed-in session is enough for v1. Step-up is a later hardening and needs no contract change, because confirm already carries the person's token.
- **Q6, public channels.** The audience rule is not relaxed. A public Slack channel's audience is every full member, so content from a role-restricted project cannot go there. `/update` to a public channel may draw only on sources every member can read (`public:org`) and on what the person types. The demo posts DBMIG status to a private channel whose members all hold a DBMIG role (`#dbmig-core`, Priya and Maya, added with the step-3 fixtures).
- **Q7, references to created items.** A reference to an item (its key, title or link) is content labelled with that item's readers, including items created earlier in the same plan: a child operation that names a parent's new item inherits the parent destination's audience as a source label, and passes P4 against it. A thread reply listing new DBMIG cards is therefore allowed in `#dbmig-core` and blocked in public `#db-migration`, where the cards themselves (drafted from the public thread) are still allowed.

## Consequences
- **Audience expansion costs time.** P4 needs every reader of a destination as a person: Jira project roles, a channel's members, a space's groups and a folder's sharing, each expanded through the identity map. It is cached per destination with the identity TTL (`BRAIN_IDENTITY_TTL_S`) and dropped on the outbox drain, like decisions. The budget is `/ask` p95 plus 2 s for a plan of 10 operations or fewer.
- **Public channels have the whole organization as their audience.** Under the rule, content from a role-restricted Jira project cannot be posted to a public channel unless every member of the organization holds that role. With today's fixtures, `/update DBMIG to:#db-migration` is blocked (Dana and Jordan cannot open DBMIG issues). This is the rule working as designed, and it shapes which demos are possible: see Q6 and Q7 above.
- **Per-person write grants.** Real Slack needs `chat:write` on each person's user token, and real Drive needs a per-person grant at `drive.file`. A person without the grant cannot post or create there.
- **The connectors gain write credentials.** These stay in Zone 2, are gitignored token files like today's Slack user tokens, and never reach the LLM plane, logs or audit payloads.
- **`transition` moves under the same executor**, keeping its stricter assignee rule on top of P1 to P3, and its tests stay green.
- **The surface of the Brain grows**: the `/actions` endpoints, the `/` menu and a preview. Every new response obeys uniform refusal (rule 7): a destination or source the person cannot see answers like one that does not exist, with similar timing.

## To verify
- **Unit tests** for P1 to P6 in `brain/policy/`, including the downgrade matrix: every pair of fixture places with different audiences, both directions.
- **Connector contract tests 9 to 12** on the simulators and the fixture connector: create as an allowed person; refused without permission; `audience` matches the seed; link-shared or external destinations are not enumerable. After every create, nobody's `check_access` on any other document has changed.
- **Leak-CI for writes:** adding, editing or removing a document the actor cannot see does not change their plan (operations, fields or policy results).
- **Red team:** injection adds a destination (rejected); injection asks for `@channel` (stripped); a source revoked between plan and confirm, or an audience widened between them (confirm refused); a plan over `max_ops`. Pass bar: **0 writes that widen an audience, 0 operations not shown in the preview.**
- **Timing:** `/actions/plan` for a denied destination and for a nonexistent one is within the same tolerance as `/ask` (`brain/timing.py`).

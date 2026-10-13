# ADR-008 · A small, explicit project registry

**Status:** Accepted (11 Oct), built in `brain/projects/`.

## Context
Nothing in the sources says that the Jira project DBMIG, the Slack channel `#db-migration` and a Confluence space are one project, so "our migration" cannot be resolved except by luck with keywords. Learning the relation automatically is unreliable and would be wrong in ways that are hard to see. A registry also has a risk of its own: listing a private channel under a project tells anyone who sees the project that the channel exists.

## Decision
- **A hand-seeded registry** (`brain/projects/registry.json`): per project an id, a name, aliases, the places that belong to it (Jira key, Slack channels, Confluence spaces, Drive folders), the tokens that make someone a member, and owners. A loader refuses anything malformed, including an alias that belongs to two projects. Changes go through review like code.
- **It never decides who sees what.** A project, and each place under it, exists for a person only if they may open at least one document inside it, using the ACL tokens the index already holds. So Sam sees no DBMIG, Priya sees DBMIG without the private leads channel, Maya sees both, and a revocation changes the view at once. An alias of a project a person cannot see matches nothing, so their refusal is the same as for a word nobody uses.
- **Boost, never filter.** A question naming a project (whole-word alias match) raises the rank of documents already found inside its visible places by a fixed amount, and adds at most three it missed. Every document added goes through the live PDP check like any hit; nothing is removed.
- **Context profile.** The model is told, by name only, which projects the question names and which the asker is a member of (`member_tokens`). "Member" is stronger than "can see": Jordan sees public pages of every project and is part of none.
- **Suggestions are proposals.** `python -m brain.projects.suggest` lists pairs of places that look related (a Jira key in a document elsewhere, cross-links, a channel name abbreviating a key) with the evidence, for an administrator to add by hand. It changes nothing and is not reachable through the API.

## Consequences
- "Our migration" works for the people in it and for nobody else; the registry does not widen access.
- The registry must be kept up to date by people; the suggestion tool shows what drift looks like.
- The real runtime translates the registry's fixture IDs for Slack and Drive through the seed manifests (`real_container`, `real_token`). **Not yet run against the real Slack and Drive.**
- Not built: a small-model alias resolver (rules only), per-project ranking weights, a UI for the registry.
- **11 Oct, Team focus:** a project may also carry `goal`, `start_date`, `target_date`, `milestone` and `sprint`, all optional and hand-written. They are shown to every member of the project, so the rule above extends to them: write nothing there that only a restricted place holds. They describe a project and still decide nothing about who sees what.

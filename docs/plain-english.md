# Internal Brain: Plain-English Plan Book

*For anyone who is not technical.*

We are building an AI assistant that answers questions from all of a company's tools at once, but only with what the person asking is allowed to see, and keeps a tamper-proof record of every answer.

## The problem
Companies lose about a third of the work week to searching for information. An AI assistant that can read everything could fix that, but it could also tell everyone everything.

Knowledge sits in four kinds of tools: documents and wikis (Confluence), tickets (Jira), chat (Slack) and shared files (Google Drive). Each has its own rules about who may see what.

- Priya, a backend engineer, asks: "What's the status of the database migration, and were there blockers in chat last week?" She should get a cited answer from places she can access.
- A contractor asks: "Show me the Q3 security breach report." It sits in a space restricted to the security team. The assistant must give away nothing, not even a hint that the report exists.

Most AI assistants copy data in once and check permissions only then. If someone loses access an hour later, they can still see the content until the next sync.

## What we are building: five promises
| Promise | In plain words |
|---|---|
| One question, all sources | Finds answers across all four tools and cites where they came from |
| Fresh | Edits appear within minutes, never an out-of-date copy |
| Private by default | You only get what you may see, and hidden things are never confirmed to exist |
| Instant revocation | Lose access, and the very next question already reflects it |
| A diary nobody can secretly edit | Compliance officers can ask what any person accessed and trust the answer |

## How it works: a bank vault with a guard
Think of the AI as a clever researcher in a reading room, and a plain, non-AI guard who controls the vault.
1. You ask a question and show your ID.
2. The guard checks what you are cleared for, at that moment, in each tool.
3. The guard fetches only those files and hands them to the researcher. The researcher never enters the vault and never holds a key.
4. The researcher writes an answer where every claim points to a file.
5. A second checker re-reads the answer to make sure nothing off-limits slipped in.
6. The visit is written in a diary. Each page carries a fingerprint of the page before it, so tearing out or changing a page is obvious.

## What makes it different
- **Re-check at answer time.** Others check permission when data is copied in. We check again at the moment of the answer.
- **Answers inherit the strictest rules.** A saved answer carries the permissions of all its sources.
- **Leak test on every release.** We automatically add and remove hidden documents and confirm a person's answer does not change.
- **Stale-answer alerts.** If a document changes after you got an answer based on it, you are told.
- **A diary that shows why.** Auditors can see not just what was allowed but the reason, including what a person could see on a past date.
- **Works inside Tencent's tools.** Staff can ask from CodeBuddy or WorkBuddy with their own identity.
- **Personal home page.** Each employee sees their own tickets, projects and channels.

## Proof
A live demo with three people (an engineer, a contractor and a security lead) asking the same question and getting three different, correct answers. We remove someone's access live and ask again. We edit a document live and show the new content appear. A built-in attack simulator tries to trick the assistant and its score is shown on screen; our target is zero leaks.

## Responsible AI
It says "I don't know" rather than guessing. It never reveals hidden content. Every claim has a source. Every decision is recorded. People stay in charge of who can see what.

Private messages are opt-in. A direct message is read only if every person in it has said yes, each with their own read-only permission. Only those people can ever get an answer from it. If one of them takes the permission back in Slack, the conversation is removed from the assistant.

## Timeline (3 people in parallel)
| Days | What gets built |
|---|---|
| 1 to 3 | Accounts, fictional company data, agreed interfaces between the three of us |
| 3 to 8 | Search across the tools, the permission guard, AI answering and answer checking |
| 8 | First working demo and a schedule review |
| 9 to 12 | Diary, Tencent tool connections, screens, attack tests, personal home page |
| 13 to 15 | Put it online in Singapore, record the demo video, submit by 16 October |

## Honest limits
No real company accounts were provided, so we use Slack and Google Drive accounts we set up ourselves, plus realistic simulators for Jira and Confluence, whose free plans lack the fine-grained permissions. Some Tencent services may be restricted outside China, so we have backups ready. We cannot prove that nothing like this exists; we claim only that we found these gaps open in the products we checked.

## Glossary
| Term | Plain meaning |
|---|---|
| Access list (ACL) | The list of who may see a document |
| Identity provider | The system that says who you are, like "Sign in with Google" |
| Role-based access (RBAC) | Permissions given by role, such as "security team", not person by person |
| Policy guard | A small non-AI program that answers "may this person see this item?" |
| Just-in-time check | Re-checking permission at the moment of the answer |
| Link authorization | Following a link to another document only if the person may see that one too |
| Webhook | A tool notifying us the moment something changes |
| p95 delay | The near-worst delay: 95% of updates are faster |
| Hash-chained log | A diary where each page carries a fingerprint of the previous page, so edits show |

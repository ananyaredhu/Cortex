# Backlog: what is left, and what to fix

Written Fri 9 Oct (Day 8) after reviewing everything on `origin/main`. Updated Sat 10 Oct (Day 9), evening: PR #38 (fixes F1, F3, F10, F11, F2 backend) is merged; branch `ws-c/ui-signin` holds the UI sign-in, the unavailable banner and a UI CI job.

Verified facts come from running the code (456 passed, 74 skipped without Docker, 10 expected failures, lint clean, no committed secrets). Items marked *(reproduced)* were demonstrated with a small script or a live run. Everything else was read in the code or the status files.

Priorities: **P0** blocks the demo or scoring. **P1** strengthens the project and should be done. **P2** stretch. Days: Day 9 = Sat 10 Oct ... Day 14 = Thu 15 Oct (freeze), Day 15 = Fri 16 Oct (submit).

---

## 1. To-do list

| ID | Pri | Task | Why | Best way to do it | Owner |
|---|---|---|---|---|---|
| T1 | P0 | **Run the product locally** and verify it | Done 10 Oct: backend in fixture mode, Node 22 installed, Docker Postgres running (554 then 565 tests pass), UI running in the browser, the 28 Playwright tests pass. Still to do: real Slack and Drive need Praew's tokens, so those stay on Praew's machine or the deployed server. Disk was 100% full, so keep an eye on it | Done 10 Oct for the backend in fixture mode (`make setup`, `make test`, `BRAIN_RUNTIME=fixture` API, scenarios 1 to 5 by curl). Still to do: install Node and run `make ui`; start Docker Desktop and run the 74 skipped tests. Real Slack and Drive need Praew's tokens, so those stay on Praew's machine or the deployed server | Ananya |
| T2 | P0 | **Proof of CodeBuddy/WorkBuddy use** | Mandatory: without proof the project is not scored. `docs/proof/` is empty | Redeem credits (check #11). Capture 3+ screenshots or a recording of build chats at milestones. Log each in `docs/07-submission.md`. Blur keys and redemption codes | All three, Guanyue leads |
| T3 | P0 | **Stateless MCP server** in `mcp_server/` (**built on branch `ws-b/mcp-server`, 10 Oct; CodeBuddy connected and its model called `search` and `ask` through it, 10 Oct; WorkBuddy untried, check #7**) | Makes WorkBuddy and CodeBuddy clients of the product. Optional for the handbook, planned for the demo | FastMCP, streamable HTTP, `stateless_http=True`. Per-request JWT. Tools: `ask`, `audit_query` first, then `search`, `get_source`, `explain_access`. Wrap the same `Runtime`; no new logic. Return structured claims and citations. Test from WorkBuddy and CodeBuddy (checks #7, #8). Needs fixes F1 and F2 first | Ananya |
| T4 | P0 | **Link-edge expansion** (**built on branch `ws-b/link-edges`, 10 Oct; the real Slack and Drive run with the seed manifests not tried yet**) | Documents store links (Jira to Slack to Confluence to Drive) but the Brain never reads them. Needed for "one ticket, its discussion, its doc, its file" | After the permission check, for each allowed top document read `documents.links`. Run every target through the PDP. Keep only allowed ones; drop denied or missing targets silently and identically. One hop, a cap per source, tagged `via_link_from`, ranked below direct hits. Never show a denied target's title. Tests: Sam never reaches SEC-17 through a visible document; Priya reaches the Slack thread from DBMIG-142 even when keywords miss. Real Slack and Drive links use real IDs, so translate them through the seed manifests | Ananya |
| T5 | P0 | **Runtime skills and loader** (4 skills) | Task-specific procedures (incident investigation, design summary, status and blockers, audit inquiry) and the two CTO questions | `brain/skills/<name>/SKILL.md` as in `docs/02-contracts/skills-format.md`. Start with a trigger-word match, upgrade to the router later. A skill carries instructions only, never authority. Pin the skill hash in the audit event | Ananya |
| T6 | P0 | **Router, query rewrite and multi-turn memory** | Each question is independent today, so follow-ups ("the second blocker?") fail | Small model rewrites the question using the last turns and the context profile. Memory entries carry ACL labels (`brain/policy/labels.py`) and are re-checked by the PDP every turn; drop an entry whose source the user can no longer see. Use the cheap model; fall back to the raw question on error | Ananya |
| T7 | P0 | **Project registry and context profile** | Nothing relates the Jira project DBMIG to the Slack channel `#db-migration` except links and text overlap, and "our migration" cannot be resolved | See section 3 below | Ananya, data from Praew |
| T8 | P1 | **Time-travel queries** (**built on branch `ws-b/time-travel`, 10 Oct**) | "What could Priya see on 12 Oct?" is a handbook-style audit question | Data already exists: `acl_snapshots` (valid_from, valid_to) for documents, and `principal_change` rows in the `ingestion_events` outbox for membership. Rebuild the user's tokens at time T by replaying events up to T, find each document's snapshot valid at T, evaluate overlap. Report the proof path. Check first that the initial-load events are complete | Ananya |
| T9 | P1 | **Audit agent** (natural language to filter) (**built on branch `ws-b/audit-agent`, 11 Oct: deterministic rule planner behind a strict plan schema; a model planner can plug in later**) | Today a typed question is only logged; the UI prefills a structured filter | A small model emits the filter JSON `{user, space, from, to, decision}`, validated against a schema. Never SQL. Compliance role only. Log its use. Also accept a bare space name (fix F14) | Ananya |
| T10 | P1 | **Retrieval relevance evaluation and retune** | Praew measured access correctness and speed at scale, not whether the right documents rank first. The vector cutoff 0.45 was tuned on 18 documents and the scale corpus used a fake embedder | See section 4 below | Ananya, with Praew's scale data |
| T11 | P1 | **Leak-CI runner and red-team scoreboard** | `/v1/leakci/latest` returns a hardcoded zero. Praew built the planting tools (`simulators/leakci.py`); nobody built the runner | Metamorphic test: plant a hidden document, ask the same question as the same user before and after, compare answer, refusal wording and latency band. Add the red-team cases (injection in a document, canary strings, revocation races). Write results to a table and expose it through the endpoint | Guanyue, tools from Praew |
| T12 | P1 | **Deployment on Tencent Cloud Singapore** (live URL earns bonus points) | Nothing is deployed. Hosting check #10 is open | Use `connectors/DEPLOY.md`. Set `BRAIN_ENV=production`, `BRAIN_DEV_AUTH` off, a stable `AUDIT_SIGNING_KEY`, a real `JWT_SIGNING_KEY` of 32+ bytes, and `CHECKER_MODEL`. Drive push needs a public HTTPS address. Run the checks after deploy | Guanyue |
| T13 | P1 | **Submission materials** | Required items are not started | Architecture and trust-boundary diagram as an image, 5 to 8 minute demo video, 16:9 cover image, description, blurb under 10 words. Ask in the WhatsApp group: are simulators acceptable, track criteria, the correct submission link (check #12) | Guanyue |
| T14 | P1 | **Checker layer 3 and a bigger layer-2 test set** | Layer 2 was validated on 40 labeled pairs; layer 3 is not built | Build 150 to 200 labeled claim and evidence pairs, including paraphrases and adversarial claims. Only add layer 3 (3B to 8B chat model, ambiguous scores only) if layer 2 misjudges too many. Time it on CPU | Ananya |
| T15 | P1 | **ADP measurements and decision records** | Cost, rate limits and the free allowance are unmeasured. ADRs are out of date | Record tokens per question, latency spread, the free quota and its expiry, and throttling behavior. Update ADR-001 (the generator is an ADP agent; TokenHub and Hunyuan not probed), ADR-007 (called by AppKey, no knowledge base), ADR-004 after the MCP server, ADR-003 | Ananya |
| T16 | P1 | **Review the contract changes** and turn on branch protection | connector-interface 0.3, api 0.2 and the audit schema changed with no review from Ananya or Guanyue | Read the changed contract files. Comment on the PRs or open a follow-up PR. Ask Guanyue to approve api 0.2. Then enable branch protection for `docs/02-contracts/` and `docs/05-decisions/` | Ananya, Guanyue |
| T17 | P2 | **Slack DMs** | The handbook describes DMs in the environment, but none of the five scenarios needs them | See section 5 below. Recommended: a simulator-only version | Praew |
| T18 | P2 | **Stretch ideas** | From the original plan | Sensitivity-aware model routing, honeytoken documents, mosaic/aggregation guard, TRTC voice, user-visible access receipts. Only after P0 and P1 | Whoever has time |

### To do if time permits

| ID | Pri | Task | Why | Best way to do it | Owner |
|---|---|---|---|---|---|
| T19 | P3 | **Membership history by group, not by person** (post-hackathon) | Today the safety-net sweep behind time-travel (`BRAIN_BASELINE_INTERVAL_S`, `Runtime.baseline`) makes one connector call per person per platform: 5 people cost about 20 calls, 1,000 people cost about 4,000 per sweep. Listing the members of each group, channel and role costs one call per group, and diffing the list against the last sweep says exactly who joined or left | Add `list_members(token)` to the connector interface (Slack channel members, Confluence group members, Jira role actors, Drive groups), invert the lists into each person's token set, write an `identity_snapshot` only where a set changed, and spread the sweep over the interval. Needs a contract change reviewed by A (connectors, simulators and the real Slack and Drive code). Until then keep the interval long (6 hours or more outside a demo) and say in the demo that production would work per group | Ananya |
| T20 | P3 | **Try the audit agent's model planner with a real key** | Built on `ws-b/audit-agent` and **off by default**; tested only against a fake endpoint, never against a real model. Unknown: whether a Tencent TokenHub/Hunyuan key is free, its rate limits, reachability from Singapore (ADR-001, check #1) | Get an API key from the Tencent Cloud console (same account as the hackathon credits), put it only in the local `.env` as `AUDIT_PLANNER_BASE_URL`, `AUDIT_PLANNER_API_KEY`, `AUDIT_PLANNER_MODEL`, run ten differently worded questions, and keep it only if latency and plans are good. The model sees only the question, people's emails and space names, never titles or events | Ananya |

---

## 2. Fix list

Status column: **done** = merged to `main`. **branch** = built on `ws-c/ui-signin`, not yet pushed or merged. **mitigated** = reduced but not eliminated. **open** = not started. Scoreboard: 6 fixed (F1, F2, F3, F10, F11 and the F8 UI job once the branch merges), 2 mitigated (F12, F15), the rest open.

| ID | Sev | Problem | Why it matters | Best fix | Status |
|---|---|---|---|---|---|
| F1 | High | **Development login is on by default** (`BRAIN_DEV_AUTH` defaults to on) and the `/sim/*` endpoints can tamper with the audit log and reset the system *(reproduced live: both calls worked with no login header)* | `Bearer dev:jordan` makes anyone the compliance officer unless a deployer remembers to switch it off | Default to off, require an explicit opt-in, add a startup check that refuses unsafe settings when `BRAIN_ENV=production` | **done**, merged (PR #38) |
| F2 | High | **No mock IdP.** The UI always sends `Bearer dev:<persona>`. The JWT path only runs in unit tests, with 8-byte keys | The sign-in layer in the architecture is not built; the MCP server needs real tokens | `/idp/token` that issues short-lived HS256 tokens per persona (opt-in). Enforce a 32-byte minimum key. Then wire the UI (needs Node) | backend **done** (PR #38); UI sign-in **done** on branch `ws-c/ui-signin` (not yet pushed) |
| F3 | High | **The audit log falsely reports tampering after a restart** *(reproduced)*. With the default empty `AUDIT_SIGNING_KEY` each start makes a new key, and `/verify` checks old checkpoints against the new one (appears at 100 events) | The "tamper-proof" demo would announce tampering by itself | Verify each checkpoint against the key it carries unless a stable key is configured (then pin to it). Require the stable key in production. Add a restart test | **done**, merged (PR #38) |
| F4 | Med | **Deleting the newest audit entries goes unnoticed** *(reproduced)*; only every 100th event is signed | An insider can delete recent history undetected | Sign more often (every event or every few), publish the latest signed head outside the database, and have `/verify` compare against it | open |
| F5 | Med | **The leak scan's denied set is almost always empty** *(reproduced)*: the prefilter removes forbidden documents first, so the scan compares against nothing | "Leak scan: clean" in the log overstates what was checked | Scan answers for canaries, IDs and titles of all documents the asker cannot see. Add a test that disables the prefilter to prove the scan catches a leak. Reword the docs | open |
| F6 | Med | **Injection sanitizer is too aggressive and too weak** *(reproduced)*: it strips "reveal the root cause of the outage" and misses paraphrases and other languages | Deletes legitimate evidence and misses simple tricks | Narrow the patterns, add benign and adversarial test sentences, and rely on the structural defences (no tools, delimiters, checker) | open |
| F7 | Med | **`/v1/leakci/latest` returns a hardcoded `leaks: 0, cases: 0`; policy versions are hardcoded** | The admin page could show "0 leaks" without any test running | Return "not run" until a real result is stored (T11) | open |
| F8 | Med | **CI has no Postgres** (about 50 tests skip) and no UI job | A green check does not mean the database code was tested | Add a `pgvector/pgvector:pg16` service, apply `db/init.sql` and `connectors/ingestion/schema.sql`, set `DATABASE_URL`. Add a UI job (install, unit tests, build) | UI job **done** on branch `ws-c/ui-signin`; the Postgres service for the Python job is still open |
| F9 | Med | **A failed freshness check silently serves the indexed copy** | The handbook says never silently serve stale content | Flag the evidence as unverified and surface it in the freshness banner; add a test | open |
| F10 | Med | **The API's `answer` prose is shown unchanged when no claim is removed**, and the checker never inspects it | An MCP client such as WorkBuddy would show unverified prose. The UI hides this by rendering the claims | Always rebuild `answer` from the verified claims | **done**, merged (PR #38) |
| F11 | Med | **A generator outage looks identical to "no source supports an answer"** | The user cannot tell a model failure from lack of evidence | A distinct `generator_unavailable` status and message, in the API and the audit event | backend **done** (PR #38); UI banner **done** on branch `ws-c/ui-signin` |
| F12 | Med | **Layer 2 is off unless `CHECKER_MODEL` is set** (the code's default is "none") | A deployment that forgets the variable silently loses a checker layer | Warn loudly at startup, and fail the production check when it is unset | **mitigated** (PR #38): production refuses to start, development warns; the code default is still `none` |
| F13 | Med | **Contract changes were merged without review; no branch protection** | The CODEOWNERS rule is never enforced | See T16 | open |
| F14 | Low | **Audit `space` filter only matches `confluence:PAY`, not `PAY`** *(reproduced)* | The scenario 5 question uses a bare space name | Accept both forms and document it in api.md | done on `ws-b/audit-agent` (11 Oct) |
| F15 | Low | **Audit log records `GENERATOR_MODEL` (default `deepseek-v3`) as the generator** though the ADP agent is GPT-5.6 Terra | The log can state the wrong model | Set `GENERATOR_MODEL` to the real agent model; warn at startup when the ADP backend is used with the default label | **mitigated** (PR #38): a startup warning only; whoever deploys must still set the real model name |
| F16 | Low | **My Work alerts are an in-memory dict**, lost on restart | Alerts vanish after a restart | Rebuild them from the audit log, as conversations are | open |
| F17 | Low | **Revocation timing claim**: "the very next query" relies on ingestion's outbox. Worst case is bounded by the 15 s decision cache and 60 s identity cache | The claim is slightly stronger than the mechanism | State it honestly in the docs. Make the TTLs configurable and shorten them for the demo | open |
| F18 | Low | **The ADP agent's "no knowledge base" setting cannot be checked from code** | A knowledge base attached in the ADP console would bypass our permissions | Verify the agent's configuration manually, and keep a dated screenshot in `docs/proof/` | open |
| F19 | Low | **Timing side channel is assumed, not measured** | Forbidden and nonexistent documents should look alike, including response time | Add a latency-band check to Leak-CI (T11) | open |
| F20 | Low | **`Makefile` `.PHONY` omits the new targets;** some docs mention Praew's local paths (`HF_HOME` on D:) | Small friction for teammates | Update `.PHONY` and make paths configurable in the README | open |
| F21 | Med | **Checker layer 2 drops a true Slack claim in 3 of 5 runs on real data** (from `docs/status/ws-c.md`, 9 Oct): the model scores that claim 18 to 30 per thousand, under the 0.05 threshold, so scenario 1 sometimes loses its Slack citation | The main demo is flaky. This is a false rejection, not a leak | Build the larger labeled claim set (T14), then retune. Options: a lower threshold for short Slack-style premises, a second pass with the layer-3 model only for dropped claims that cite one source, or keep a claim that passes layer 1 but scores low and flag it "lower confidence" instead of dropping it. Measure false rejections and false accepts together | open |

---

## 3. Project registry and context profile (T7): best way to build it

**Problem.** Nothing explicitly says that the Jira project DBMIG, the Slack channel `#db-migration`, the Confluence space ENG and the Drive folder belong to one project. "Our migration" cannot be resolved.

**Best way: a small, explicit project registry, seeded by hand and extended with suggestions.**
1. **Data:** `projects(project_id, name, aliases[], jira_project, slack_channels[], confluence_spaces[], drive_folders[], owners[])`. Seed it for Company A from a config file next to the fixtures (`docs/company-a-seed.md` has the story).
2. **Suggestions:** derive candidate links automatically from the link graph (documents that link each other), Jira keys mentioned in Slack text (`[A-Z]+-\d+`), and name similarity. Show them as proposals, never as facts.
3. **Use:** in the retrieve step, resolve the question to projects (alias match first, small model optional), expand the search terms with aliases, and **boost** documents whose `parent_id` belongs to those projects. Boost, never filter.
4. **Context profile:** per user, the projects they are part of, built only from documents and identities they may see.
5. **Access rule:** the registry itself can leak. A private channel mapped to a project would reveal that it exists. Include a registry entry for a user only if they hold the matching access (for example `channel:C_DBMIGPRIV`).
6. **Tests:** Sam never sees DBMIG's private channel in any profile; "our migration" resolves for Priya and Maya.

---

## 4. Relevance at scale (T10): what it means and how to tackle it

**What it means.** Praew showed that each persona's filter returns exactly the documents the spec allows, and that queries are fast. That is access correctness and speed. Relevance is a different question: for a given question, does the correct document appear in the top results among 12,000 pages? With many pages mentioning "migration" or "payment", the top 8 candidates could be wrong. The generator then either answers from the wrong evidence or abstains.

**The cutoff.** A chunk counts as a vector match only if its distance is at most 0.45. That number was chosen on 18 documents (relevant ones at 0.26 to 0.44, everything else at 0.48 and above). In a large corpus, many partly related pages will land around 0.40 to 0.50, so a fixed cutoff either lets noise in or cuts good documents. The scale corpus was also embedded with a fake embedder, so no real vector relevance has been measured at scale.

**How to tackle it, cheapest first.**
1. **Keyword-only test now:** run the nine golden cases against the scale index with the vector leg off. Do the right documents still appear with 12,000 distractors?
2. **A small labeled set:** 30 to 50 questions with known correct document IDs. `simulators/scale.py` is deterministic, so ground truth is known. Measure recall at 8 (is the right document a candidate) and mean reciprocal rank.
3. **Real embeddings on a slice:** embedding the full corpus takes about 8 hours. Embed a slice (about 1,500 documents plus the Company A documents) overnight instead, then compare cutoffs.
4. **Make the cutoff relative:** for example, keep chunks within a margin of the best distance, or keep a fixed number by rank, and compare.
5. **Optional reranker:** a local `bge-reranker-v2-m3` over the top candidates, if recall is fine but ordering is poor.
6. **Record the result** in `docs/` and the decision records.

---

## 5. Slack DMs (T17): what it means and how to implement it

**Scopes.** A Slack app asks for permissions called OAuth scopes. Reading channels needs `channels:history`, private channels `groups:history`, and direct messages `im:history` and `mpim:history` (plus the matching `read` scopes). A bot token can only read conversations the bot belongs to, and a bot cannot join a DM between two people. Reading DMs therefore needs each person to authorize the app with their own user token. Reading everyone's DMs centrally needs an organization-level export or compliance feature on a paid Slack plan. This is from my knowledge of Slack, so verify it before relying on it.

**It is also sensitive.** DMs are the most private content a company has. Ingesting them means the Brain stores private conversations, which needs consent and a clear statement in the Responsible-AI section.

**Recommended approach.** Add DMs to the **Slack simulator only**, with participants-only access rules, to show the permission semantics without real DM scopes: access tokens for both participants (`user:<email>` for each), `check_access` true only for participants, plus a test that a third person never sees it. Describe real DM ingestion (per-user consent and user scopes) in the docs as the production path.

**If real DMs are wanted anyway.** Each persona who authorizes the app gets user scopes `im:read`, `im:history`, `mpim:read`, `mpim:history`. The connector lists DMs per user token, deduplicates by channel ID, and sets the access tokens to the two participants. Default to opt-in.

---

## 6. Suggested order (to the Thu 15 Oct freeze)

| Day | Focus |
|---|---|
| Day 9 (Sat 10) | T1 finish (Node, Docker). The fix PR: F1, F2 (backend), F3, F10, F11, F12. Start T2 and T13 questions with the others |
| Day 10 to 11 | T16 review contracts. T3 MCP server. T4 link expansion |
| Day 11 to 12 | T5 skills. T6 router and memory. T7 registry and profile |
| Day 12 to 13 | T8 time-travel. T9 audit agent. T10 relevance test (overnight slice). T11 Leak-CI with Guanyue |
| Day 13 to 14 | Integration, T12 deploy, T13 materials, proof, remaining fixes. Freeze Thu 15 Oct |
| Day 15 | Final dry run and submit |

If time slips, defer in this order: T18, T14 layer 3, T9's natural-language part, T17, then T8.

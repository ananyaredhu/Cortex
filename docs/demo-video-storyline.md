# Demo video storyline

For whoever designs and cuts the video (Claude Design, with this repository). Written 13 Oct from the code on `ws-c/routines-templates`.

- **Length:** 7:00 target, inside the required 5 to 8 minutes. Each scene has a time budget; the cut list at the end says what to drop first.
- **Must contain** (`docs/07-submission.md`): the case study named at the start ("The Internal Brain"), an overview, the core features, and a reflection on how it was built with CodeBuddy and WorkBuddy, with tips.
- **Product name on screen:** Cortex. The case study name appears once, on the title card.
- **Voice:** calm, plain, second person where it helps ("you ask once"). No hype words. Let the screen prove the claim, then say it.

## The one idea

**Cortex answers from everything your company knows, and only from what you could open yourself.**

Every scene is the same promise seen from a different chair: the engineer who asks, the contractor who must not see, the engineer whose access changes, the compliance officer who checks afterwards. If a shot does not serve that sentence, cut it.

## Cast (fixture personas, `ui/src/auth/personas.ts`)

| Person | Role | What they are for |
|---|---|---|
| Priya | Backend engineer | The main character. Asks, works, sets a routine. |
| Sam | Contractor (guest, no Slack account) | The negative case: asks for something he may not see. |
| Dana | Security lead | Sees the security space; shows Admin. |
| Jordan | Compliance officer | Reconstructs what happened from the audit log. |
| Maya | Engineering manager | Appears only as an assignee on tickets. |

Company A is fictional. Its story, already in the fixtures: a database migration (DBMIG) with a cutover blocked on replica lag, and a payment outage last quarter (PAYINC) caused by connection pool exhaustion after a retry storm.

## Scene by scene

Times are cumulative. "Say" is voiceover; "Show" is what is on screen; "Proof" is the detail the viewer must be able to read, so hold on it or zoom.

### 0 · Cold open (0:00 to 0:30)
- **Show:** four windows side by side, each a different tool: a Jira ticket, a Slack thread, a Confluence page, a Drive document (the simulators and the fixture pages will do). A cursor hunts between them. Cut to black, then the title card: *Cortex* · "Case study: The Internal Brain" · team name.
- **Say:** "The answer to most questions at work already exists. It is spread across four tools, and not everyone is allowed to see all of it. An assistant that searches everything is easy to build. One that never shows you what you could not open yourself is the hard part. That is what we built."

### 1 · One question, one cited answer (0:30 to 1:25) · scenario 1
- **Show:** Cortex as Priya, on Ask. The empty page: greeting, question box, three suggested questions beneath. Click the first: "What's the status of the database migration, and were there blockers raised in Slack last week?" The five-step progress line runs. The answer arrives with numbered citations; hover one to show the quoted excerpt. Then the Trust panel on the right: sources searched, freshness per source, grounding.
- **Say:** "Priya asks one question in her own words. Cortex reads Jira and Slack for her and answers with a citation on every statement. The panel on the right shows where the answer came from, how fresh each source is, and how much of it was checked against a source she can open."
- **Proof:** citations from both Jira and Slack; "Why can I see this?" on one citation, showing the access path. Nothing from the private channel she is not in.

### 2 · Same question, different people (1:25 to 2:15) · scenario 3
- **Show:** Compare people (split screen; needs demo mode). Type: "Show me the security incident report from the Q3 breach". Three columns answer at once: Priya, Sam, Dana.
- **Say:** "The same question, asked by three people. Dana leads security, so she gets the report. Sam is a contractor. His answer does not say access denied, because that would tell him the report exists. He gets exactly what he would get if there were no report at all: same words, same shape, same timing."
- **Proof:** Dana's column cites the breach report; Sam's column is the plain "nothing I can answer from" response with no title, count or hint. Hold on Sam's column for two full seconds.

### 3 · The answer follows the truth (2:15 to 3:15) · scenarios 2 and 4
Two short beats, back as Priya.
- **Beat A, freshness. Show:** ask "What's the latest runbook for payment-service incident failover?" and note the "as of" time. Open Demo controls, fire the scripted change (the runbook's owner adds a failover step). Ask again: the answer now has the new step and a later "as of". Cut to My work: "Needs you" says the earlier answer changed, with "Ask again".
- **Say:** "Documents change. When the runbook's owner adds a step, the next answer has it, with the time it is true as of. And Priya is told that the answer she got this morning is now out of date."
- **Beat B, revocation. Show:** ask about the auth design discussion; the answer cites a thread in the private channel `#auth-private`. In Demo controls, remove Priya from that channel. Ask the same question again: the citation and its content are gone.
- **Say:** "Access changes too. Priya is removed from a private channel. On her very next question, what she learned from it is gone. Nothing is cached past the permission that allowed it."
- **Proof:** the before and after answers side by side in the edit, with the vanished citation circled.

### 4 · Where the work is (3:15 to 4:15)
- **Show, My work:** the "Your workspace" filter bar; click `DBMIG` and the whole page narrows to that project. The tickets table: key, status, assignee, due date, priority in aligned columns, an overdue ticket marked. Drag a ticket and a Slack thread into "Current focus".
- **Show, Team focus:** the timeline across projects, then the board. Drag Priya's own ticket "Raise connection pool alerts" from To Do to In Progress. Try to drag one of Maya's: it does not move.
- **Say:** "Cortex is also where the day starts. Priya's tickets, pages and threads from every tool, in one place, and she chooses what to focus on. On the team board she can move her own ticket, and the change is made in Jira. She cannot move anyone else's. Even here, every number on the page is counted only from what she is allowed to open."
- **Proof:** the board note "Drag your own tickets to change their status. The change is made in Jira."

### 5 · Routines (4:15 to 4:50)
- **Show:** Routines, Templates tab. Pick "Morning brief": the form fills with instructions and "every weekday, 09:00". Save, then "Run now". The brief appears as a normal cited answer.
- **Say:** "A question worth asking every morning can ask itself. A routine runs on a schedule as Priya, and her access is checked again each time it runs, never when it was set up. If she loses access tomorrow, tomorrow's brief loses it too."
- **Proof:** the schedule line on the routine and the citations on the run.

### 6 · Who saw what (4:50 to 5:45) · scenario 5
- **Show:** switch to Jordan. Audit console. Type: "Show me everything Priya accessed in the PAY Confluence space in the last 30 days". The "Understood as:" line shows the filter it became. Open one event: each document with allowed or denied, the reason, and the access path; denied documents appear only as hashes. The integrity widget says "Log integrity verified". In Demo controls, "Tamper with entry". Verify again: it fails and names the broken entry.
- **Say:** "Every question is recorded, with every document that was considered and why it was allowed or refused. Jordan asks the log in plain language. Documents a person was refused are stored as hashes, so the audit trail does not become a leak of its own. And the log is chained: change one entry and the check fails."
- **Proof:** the allowed and denied rows in the drawer; the verified state turning to failed.

### 7 · How it holds (5:45 to 6:25)
- **Show:** the Admin page as Dana for five seconds: connector health, freshness against the target, the trap tests (Leak-CI) result. Then the architecture diagram (from `docs/01-architecture.md`) drawn as two zones with a line between them.
- **Say:** "The rule that makes this safe is a line through the middle of the system. On one side, plain tested code decides who may see what. There is no language model on that side. On the other side, the model writes the answer, and all it ever receives is text the asker was already allowed to read. A model cannot leak what it was never given. Trap tests plant hidden documents and check, on every change, that no answer moves."
- **Proof:** the two zones labelled "Policy: no model" and "Model: authorised context only", and the Leak-CI line on Admin.

### 8 · How we built it, and close (6:25 to 7:00)
- **Show:** three or four real CodeBuddy and WorkBuddy chat screenshots from `docs/proof/` (keys blurred), then Cortex's `ask` tool being called from WorkBuddy over MCP, then the title card again.
- **Say:** "We built Cortex with CodeBuddy and WorkBuddy. Three things worked for us. Write the contract first and let the agent build to it. Keep the agent inside the workspace and approve what it does. Use the free models for routine edits and save the large ones for design. Cortex also runs inside WorkBuddy as a tool, with the same rules. One brain for the company, and it only tells you what you could have opened yourself."

## Recording setup

| Need | How |
|---|---|
| Compare people and the Demo controls drawer | Run the UI in demo mode: `npm run dev:demo` (plain `npm run dev` hides both). |
| The scripted changes (runbook edit, channel removal, tamper) | They need the stub or fixture API (`make stub-api` or `make brain-api-fixture`); they are skipped on the real stack. |
| Model-written answers | Only the real stack (`make brain-api`) uses the model. The fixture API writes answers from templates, which read stiffly. |
| Draggable board cards | The index must be from 12 Oct or later. On the real stack run `python -m connectors.ingestion --once --sources jira --recrawl` first. |
| Clean state | Reset from Demo controls between takes; clear Current focus (browser storage `cortex.focus.<persona>`). |

**Suggested split:** record scenes 1, 4 and 5 on the real stack for natural answers, and scenes 2, 3 and 6 on the fixture stack for the scripted changes. The UI is identical, so the cut is invisible. Record at 1440×900; pick one theme and keep it (light reads better on video).

## Check before recording

These are in the code but were not confirmed for this script. Run each once and adjust the scene if it differs.

1. The exact wording of Sam's answer in scene 2 and that Dana's column cites the breach report.
2. Which scripted changes the Demo controls offer by name, and that the "Needs you" alert appears after the runbook change.
3. The revocation beat: which question cites `#auth-private` for Priya before the removal.
4. "Run now" on a routine returns a cited answer on the stack you record on.
5. The tamper step makes the integrity check fail visibly.
6. The Leak-CI line on Admin shows a real run, not the placeholder.
7. The MCP call from WorkBuddy in scene 8 (the backlog lists WorkBuddy as untried).
8. The three build tips are the team's own; replace them with what actually helped.
9. `docs/proof/` has approved screenshots with no keys in them.

## If it runs long, cut in this order

1. Scene 3, beat A (freshness): keep revocation, which is the stronger picture. Saves about 30 s.
2. Scene 7, the Admin shot: go straight to the diagram. Saves about 10 s.
3. Scene 4, the Team focus timeline: go straight to the board. Saves about 10 s.
4. Scene 5 (routines) down to one sentence over the template being picked. Saves about 20 s.

Never cut: scene 2 (the contractor), the tamper check in scene 6, and the reflection in scene 8, which the submission requires.

## Design notes

- **Captions:** one line per scene naming the chair we are in ("Priya · backend engineer"), bottom left, in the Cortex type. Persona switches are the story's chapter marks, so make them visible: a brief avatar card on each switch.
- **Zooms:** the proofs are small text. Plan a zoom for each "Proof" line above; do not rely on the viewer reading a full-screen capture.
- **Colour:** Cortex orange is the only accent. Green, amber and red on screen mean trust state in the product, so do not use them for decoration in titles or callouts.
- **Third-party logos:** the product shows the Jira, Confluence, Slack and Drive marks to say where a document lives. Keep them at interface size; do not use them in title cards or the cover.
- **No invented content:** every answer, ticket and name on screen should come from the running product. Do not mock up a nicer answer in post.

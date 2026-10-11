# Full end-to-end test on the real stack (runbook)

**For:** whoever runs it (Praew's laptop: it has the real Slack workspace, the Google accounts, the identity map and the seed manifests).
**Goal:** prove every piece works together with real Postgres, real Slack and Drive, bge-m3, the ADP agent and the UI.
**Time:** about 90 minutes. **Do the steps in order**: some depend on the one before, and two steps cannot run at the same time as ingestion.

Three rules that avoid most trouble:
1. **One ingestion process at a time.** It is the only writer to the index. Never start a second.
2. **Go gently with ADP.** It silently drops requests above an unpublished rate and then stays throttled for minutes. Every step below that asks questions pauses between them. If answers show "the answer service is unavailable", wait 5 minutes before trying again. Do not rerun a step in a hurry.
3. **Never paste a token, key or screenshot with one in it** into chat, an issue or a commit.

Every command is run from the repository root, in a terminal where `.venv` exists (`make setup` once if it does not). Lines starting with `#` are comments, not commands.

**On Windows** (Praew's laptop) there is no `make`, and `python` must be the one in `.venv`. Use Git Bash and these instead:

| The runbook says | Run |
|---|---|
| `python -m ...` | `.venv/Scripts/python.exe -m ...` |
| `make db-up` | `docker compose up -d` |
| `make sim-confluence` | `.venv/Scripts/python.exe -m uvicorn simulators.confluence.app:app --port 8101` |
| `make sim-jira` | `.venv/Scripts/python.exe -m uvicorn simulators.jira.app:app --port 8102` |
| `make brain-api` | `.venv/Scripts/python.exe -m uvicorn brain.api.main:app --port 8000` |

Set `PYTHONUTF8=1` in each terminal first (`export PYTHONUTF8=1`), or a printed arrow or dash can stop a script.

**On an 8 GB machine** the Brain and the ingestion process each load bge-m3 (about 3.5 GB of memory each, committed). Close the browser tabs and other apps you can before step 3, never run a second Brain, and if a command dies with "Segmentation fault" or the UI stops with "JavaScript heap out of memory", that is the machine running out of memory, not the product: free some and run the step again. Leak-CI's timing check (step 8) is only meaningful when the machine is not swapping.

---

## Step 0 · Before you start (10 minutes)
**0.1 Update the code and check you are on the new main**
```bash
git fetch origin && git checkout main && git reset --hard origin/main
git log --oneline -1          # the top commit must be the merge of the runbook's pull request (#54, 11 Oct) or a later one
```

**0.2 Check that your private files are there** (they are gitignored, so they must be on this laptop):
```bash
ls connectors/identity-map.local.json connectors/slack/seed-manifest.local.json \
   connectors/gdrive/seed-manifest.local.json connectors/gdrive/gdrive.local.json
ls connectors/gdrive/token-gdrive-*.json
```
All of them must exist. If one is missing, stop: that account has not been set up here.

**0.3 Check `.env`** (copy from `.env.example` if you have none). These must have a value; check each by opening the file, never by printing it:
`DATABASE_URL`, `EMBEDDING_BACKEND=bge-m3`, `HF_HOME` (a folder with 5 GB free), `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `ADP_APP_KEY`, `CHECKER_MODEL=cross-encoder/nli-deberta-v3-xsmall`.
Add these for this test:
```
JWT_SIGNING_KEY=<64 random hex characters>      # make one with:  .venv/bin/python -c "import secrets; print(secrets.token_hex(32))"
BRAIN_MOCK_IDP=1
BRAIN_DEV_AUTH=1                                # the Playwright tests (step 9) send "dev:" tokens; turn it off again for the demo
BRAIN_MCP=1
SIM_ADMIN_TOKEN=<any long random string>        # the simulators' admin endpoints need it
```
**Pass:** nothing is missing. **If not:** do not continue, ask Ananya.

**0.4 Free disk and memory.** About 6 GB of disk and 4 GB of free memory (bge-m3 uses about 2 GB, the checker about 1 GB).

---

## Step 1 · Start the database and the two simulators
Use four separate terminal windows (call them T1 to T4). Keep them open.
```bash
# T1: Postgres with pgvector (Docker Desktop must be running)
make db-up
docker ps                 # expect a container named like "...-db-1" with status "Up ... (healthy)"
```
```bash
# T2: the Confluence simulator (port 8101)
SIM_ADMIN_TOKEN=<the same value as in .env> make sim-confluence
```
```bash
# T3: the Jira simulator (port 8102)
SIM_ADMIN_TOKEN=<the same value as in .env> make sim-jira
```
If the simulators were already running from before 12 Oct, stop and start them: the Jira simulator's seed now gives each issue its status and assignee as fields, and one started earlier has none (the answers still work, but the check on a wrongly stated status has nothing to compare with).

**Pass:** T2 and T3 print "Application startup complete" and keep running. Check from any terminal:
```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8101/docs      # 200
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8102/docs      # 200
```

## Step 2 · Reset to the seeded story, and fix what only a person can fix
```bash
python -m connectors.reset_demo
```
This lists everything that differs from the seed (simulators, real Slack, real Drive). It changes nothing. Read it.
- Anything about **Slack** (a missing reply, Priya not in `#auth-private`, an extra thread) cannot be fixed by code: fix it by hand in Slack, then run the command again.
- When only things that code can fix remain, apply them:
```bash
python -m connectors.reset_demo --apply
```
**Pass:** the check ends with no differences and exit code 0 (`echo $?` prints 0). It also ingests and runs `check_index`. **Before scenario 4 later, Priya must be in `#auth-private`.**

## Step 3 · Run ingestion, and check the index
```bash
# T4: the one ingestion process
python -m connectors.ingestion --poll 120 --sources confluence,jira,slack,gdrive --slack-events
```
Leave it running. It polls every 2 minutes: the freshness target is 5 minutes, and with a longer poll the Trust panel shows the sources as "Behind" between passes and the runbook edit in step 10 takes as long as the poll to appear (the first run used `--poll 600` and measured 607 s). The first pass embeds the documents with bge-m3: expect 1 to 3 minutes for the 18 Company A documents. When the log stops printing new lines, check the index from another terminal:
```bash
python -m connectors.ingestion.check_index
```
**Pass:** it reports that the index matches the seeded story (every fixture document present, with the right access tokens). **If a Slack or Drive document is missing:** check the ingestion log for a named source error, then see `connectors/DEPLOY.md`, "Is it healthy?".

## Step 4 · Start the Brain in real mode
```bash
# T5: the Brain (loads bge-m3 and the checker first: 1 to 3 minutes, longer the first time)
make brain-api
```
**Pass:** the log shows the server listening on port 8000 and no startup error. Then:
```bash
curl -s http://localhost:8000/v1/health                      # {"status":"ok"...}
curl -s -X POST http://localhost:8000/idp/token -H 'Content-Type: application/json' -d '{"persona":"priya"}' | head -c 60; echo
```
**Pass:** the second prints the beginning of a token (do not copy it anywhere). **If the Brain refuses to start**, it prints which setting is unsafe or missing.

## Step 5 · The golden cases
Nine scripted questions with expected citations. Pace: one case every 11 seconds, so ADP is not throttled.
```bash
python -m evals.golden --target http://localhost:8000
```
**Pass:** every line starts with `pass`, the last line says `... failed 0`, and the exit code is 0. Lines starting with `hand` are the scripted cases (the runbook edit and Priya leaving a channel): they need a change in a platform, so they are done by hand in step 10. **If a case says `FAIL`:** it prints why (a missing or forbidden citation, or forbidden text). Send that line to Ananya.

## Step 6 · Red team (attacks through the API)
Three suites of attacks (`evals/redteam.py`, cases in `evals/redteam_cases.json`): an injection planted in a Slack message (10 questions), injections typed as the question (22), and a forbidden document against one that does not exist (4 pairs, plus two checks of the access explainer). About 45 questions, 11 seconds apart, so about 12 minutes. It plants nothing and writes nothing: ingestion keeps running.
```bash
python -m evals.redteam --target http://localhost:8000
```
**Pass:** the three suites say `... passed, 0 failed` and the last line starts with `0 leaks, 0 failed probes`. The suite `revocation-race` is **skipped** on a real stack (it needs the development controls; scenario 4 in step 10 covers it by hand): a `skip` line for it is normal. A line starting with `LEAK` is never acceptable: send it to Ananya at once. A line starting with `FAIL` that says "the answer service was unavailable" means ADP throttled the run: wait 5 minutes and rerun just that suite, for example `--only injection-question`. The result is also written to `evals/scoreboard/redteam-latest.json`.

## Step 7 · MCP server
```bash
python -m mcp_server.smoke --url http://localhost:8000
```
**Pass:** all 11 checks print `PASS`. The check script translates the real Slack and Drive IDs back to the fixture IDs using the seed manifests from Step 2. If a line fails, send it to Ananya.

## Step 8 · Leak-CI (needs ingestion stopped)
Leak-CI plants hidden documents, ingests them itself, asks, edits, removes and asks again. **It cannot run while the ingestion process is running** (two writers).
1. In T4, stop ingestion with Ctrl+C.
2. Run:
```bash
python -m evals.leakci --target http://localhost:8000
```
3. When it ends, start ingestion again in T4 with the same command as in step 3, wait for it to catch up, and run `python -m connectors.ingestion.check_index` once more.

**Pass:** the last line says `0 leaks, 0 failed checks in 6 cases`. Leak-CI writes the scoreboard that the Admin page shows. **If a "control" case fails once and passes on the second try**, that is the known flakiness described in `docs/status/ws-c.md`; a leak is never acceptable: send it to Ananya at once.

## Step 9 · The UI, and the browser tests
```bash
# T6: the UI in sign-in mode (port 5173)
cd ui && npm install && npm run dev:idp
```
Open http://localhost:5173 in a browser. Pick a person from the switcher at the top (Priya, Sam, Dana, Jordan, Maya): the app signs in as them through the mock IdP.

Optional browser tests: they ask about 25 questions, so ADP will be slow (calls queue at 6 a minute, about 5 minutes). They sign in with `dev:` tokens, so the Brain must have been started with `BRAIN_DEV_AUTH=1`. After `npm install` Playwright may need its browser again (about 150 MB into `%LOCALAPPDATA%/ms-playwright`; set `PLAYWRIGHT_BROWSERS_PATH` to a folder on D: if C: is full): without it every test fails at once with "Executable doesn't exist". Run only the scenarios file:
```bash
cd ui && npx playwright install chromium
cd ui && API_TARGET=http://localhost:8000 npx playwright test e2e/scenarios.spec.ts e2e/uniform-refusal.spec.ts
```
Tests that need the scripted events (`/sim/advance`) skip themselves on a real stack.
**Pass:** the tests that run pass; skips are normal.

## Step 10 · The scenarios by hand (the part judges will see)
Do these in the UI, in order, leaving 15 to 20 seconds between questions. For each one write down what you saw, and screenshot the answer (no keys in the shot).

| # | As | Do | Pass looks like |
|---|---|---|---|
| 1 | Priya | Ask: "What's the status of the database migration, and were there blockers raised in Slack last week?" | One answer with citations to Jira (DBMIG-142) and Slack (`#db-migration`). Nothing from the private leads channel. Open the Trust panel: grounding shown, sources listed. |
| 1b | Priya | Then ask only: "why?" in the same conversation | An answer that is still about the migration, not a refusal (follow-up memory). |
| 2 | Priya | Edit the payment-service runbook (Confluence simulator: add a failover step; `docs/04-scenarios.md`), wait for ingestion (up to 2 minutes, the poll in step 3; the ingestion log prints `"confluence": {"indexed": 1}`), ask: "What's the latest runbook for payment-service incident failover?" | The new step is in the answer, with an "as of" time. |
| 3 | Sam, then Priya | Sam asks: "Show me the security incident report from the Q3 breach". Then Sam asks: "Show me the Q9 quantum hologram audit report" | Both answers are the **same refusal**, with the same layout. Nothing about the breach. |
| 4 | Priya | Ask the auth question: "What are the open concerns in the auth service threat model?" (cites `#auth-private`). Then **remove Priya from `#auth-private` in Slack** (admin). Wait for the event (seconds), ask again | The private thread is gone on the next answer. |
| 4b | any | Afterwards put Priya back in `#auth-private` | The thread comes back. |
| 5 | Jordan | Audit console, type: "What did Priya access in the PAY space in the last 30 days?" then press **Ask** (not "Run query", which uses the filters under the box as they are) | An "Understood as: ..." line, a summary in bold, a table of Priya's questions with allow and deny decisions. "Log integrity verified" shows. |
| 5b | Jordan | Type "What could Priya see on <a date today>?" and press **Ask** | Three lists: what she could open then, what she lost since, what she gained since. A document Jordan cannot open shows its ID and "restricted", not its title. |
| 6 | Priya | CTO 1: "What was the root cause of the payment outage last quarter, and what follow-up tickets were created?" | Root cause (connection pool exhaustion after a retry storm), tickets PAYINC-10 and 11, citations from Jira, Slack and Drive. |
| 7 | Priya | CTO 2: "Summarize the design discussion around the new auth service from last sprint's Slack threads and link the Confluence decision doc" | The threads summarized and the decision document cited. |
| 8 | Priya | Ask: "How is our migration going?" | An answer about the DBMIG project (the project registry). Sam asking the same gets a refusal. |
| 9 | My Work | Open My Work as Priya, then as Sam | Priya sees her projects by name and her channels by name (`db-migration`, not a Slack ID); Sam sees none. An "answer changed" alert shows only for a question she has not asked again since the change. |

**Pass:** every row looks as described. Record any difference with the question you asked and the time.

## Step 11 · Leave things tidy
- Turn `BRAIN_DEV_AUTH` back to `0` in `.env` for the demo.
- Stop T5 and T6 when finished. Keep T4 (ingestion) running only while you use the stack.
- Put Priya back in `#auth-private` if she is not there.

---

## What to send back
A short list: the step number, the result (pass or fail), and for a failure the **exact printed line** and the time. Paste them to Ananya (no tokens, no screenshots with keys). The most valuable results are steps 5, 6 and 8 (they print counts) and the screenshots from step 10.

## If something goes wrong
| You see | Likely cause | Do |
|---|---|---|
| "the answer service is unavailable" in answers | ADP throttled it (too many calls) | Wait 5 minutes, ask one question at a time |
| The Brain exits at start with a list of problems | An unsafe or missing setting | Fix the setting it names (`.env`) |
| `check_index` reports a missing document | Ingestion has not caught up, or a source failed | Look at the ingestion log for the source's error; `connectors/DEPLOY.md` |
| Leak-CI says the index "has one writer" | Ingestion is still running | Stop it (step 8) |
| Slack questions find nothing | The bot token or the identity map is wrong, or Slack events are off | `connectors/slack/README.md` |
| Everything is slow the first time | bge-m3 and the checker are loading | Wait for the Brain's startup to finish |

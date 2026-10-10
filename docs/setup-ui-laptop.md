# Set up a laptop for UI work (no model downloads)

For a teammate working on the product UI in `ui/`. You need three programs, one clone, and two
terminal windows. Nothing here downloads the embedding model or needs a database or API keys.

## 1. Install three programs (once)
1. **Git**: https://git-scm.com/download/win. Keep every default. (Mac: open Terminal, type `git`, accept the install prompt.)
2. **Node.js LTS** (v22): https://nodejs.org. Keep every default.
3. **Python 3.12**: https://www.python.org/downloads/. On the first screen tick **"Add python.exe to PATH"**, then Install Now.
4. Optional but recommended: **Visual Studio Code**: https://code.visualstudio.com.

**Installing on a drive other than C: is fine.** Git: change the folder on the "Select Destination Location" screen (for example `D:\Program Files\Git`). Node: change it on the "Destination Folder" screen (`D:\Program Files\nodejs\`). Python: choose "Customize installation", keep the PATH tick, and on Advanced Options tick "Install for all users" and set the location (`D:\Python312`). Clone the project onto that drive too (step 2); `.venv` and `ui\node_modules` are created inside the clone. To keep the package caches off C: as well:
```
npm config set cache D:\npm-cache
pip config set global.cache-dir D:\pip-cache
```

Close and reopen any terminal after installing. Check it worked:
```
git --version
node --version
python --version
```
Each should print a version number.

## 2. Get the code
Open **PowerShell** (Windows) or **Terminal** (Mac). Pick a folder for projects, then clone:
```
cd ~\Documents
git clone https://github.com/ananyaredhu/Internal-Brain.git
cd Internal-Brain
```
The repo is public, so cloning needs no login. Pushing later does: Praew or Ananya must add your
GitHub account as a collaborator on `ananyaredhu/Internal-Brain`, and the first `git push` will open
a browser window to sign in.

Tell Git who you are (shown on your commits):
```
git config --global user.name "Your Name"
git config --global user.email "you@example.com"
```

## 3. Backend (the stub API) — terminal window 1
Run from the `Internal-Brain` folder. This installs small Python packages only.
```
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\pre-commit install
.venv\Scripts\uvicorn brain.stub_api.app:app --reload --port 8000
```
(Mac: replace `.venv\Scripts\` with `.venv/bin/`.)

Leave this window open. It should end with `Uvicorn running on http://127.0.0.1:8000`.
Do **not** run `pip install -r requirements-embed.txt`; that is the 4 GB model and is not needed for UI work.
No `.env` file is needed for the stub.

## 4. Frontend (the UI) — terminal window 2
Open a second terminal, go to the same folder, then:
```
cd Internal-Brain\ui
npm install
npm run dev:demo
```
Open http://localhost:5173 in a browser. `dev:demo` adds the demo controls drawer (bottom left)
for the scripted scenarios; plain `npm run dev` hides it.

Pick a persona with the switcher at bottom left. The stub answers with fixture data, so no keys.

## 5. Every day
Start the two windows again (steps 3 last line and 4 last line; skip the installs). Then:
```
git checkout main
git pull
git checkout -b ws-c/<short-topic>
```
Edit files under `ui/src/`. The browser refreshes by itself when you save.

Before you push:
```
npm run typecheck
npm test
```

Save and share your work:
```
git add -A
git commit -m "What you changed, in one line"
git push -u origin ws-c/<short-topic>
```
Then open https://github.com/ananyaredhu/Internal-Brain, click **Compare & pull request**, and ask for a review.

## 6. Where things are
- `ui/README.md`: how the UI is organised and where each screen lives.
- `ui/MOCKUP-DRIFT.md`: read first; where the mockup and the repo disagree.
- `ui/design-reference/`: the Cortex design system and mockup screens.
- `docs/03-workstreams/ws-c-experience.md`: the Workstream C task list.

## 7. Rules
- Never commit `.env`, `credentials.json`, or any key. They are ignored by Git; keep it that way.
- Branches are `ws-c/<topic>`; everything reaches `main` through a pull request.
- If a command fails, copy the whole red text and send it to Praew.

## Troubleshooting
- **`python` opens the Microsoft Store**: Python is not on PATH. Reinstall and tick "Add python.exe to PATH".
- **`running scripts is disabled on this system`**: you ran `Activate.ps1`. Not needed; use the `.venv\Scripts\...` paths above.
- **Port 8000 or 5173 already in use**: close the other window using it, or run the stub with `--port 8020` and the UI with `$env:API_TARGET="http://localhost:8020"; npm run dev:demo`.
- **UI shows "unreachable" banners**: window 1 (the stub) is not running.
- **`npm install` asks to install Playwright browsers**: it does not; only `npm run e2e` needs them. Skip that unless asked.

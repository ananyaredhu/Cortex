import { defineConfig, devices } from "@playwright/test";
import path from "node:path";

// End-to-end runs of the demo scenarios through the UI, against the stub API.
// Starts its own stub and UI on spare ports, so a running `make stub-api` / `npm run dev` is left alone.
// Set API_TARGET to run the same specs against another API: the real Brain (`make brain-api`, every answer
// comes from the model and takes 5 to 20 s, so the timeouts are longer) or the fixture runtime
// (`make brain-api-fixture`, which also has the scripted events). Tests that need `/sim/advance` skip
// on the real Brain; see e2e/helpers.ts.
const STUB_PORT = 8010;
const UI_PORT = 5180;
const root = path.resolve(import.meta.dirname, "..");
const python = path.join(root, ".venv", ...(process.platform === "win32" ? ["Scripts", "python.exe"] : ["bin", "python"]));
const apiTarget = process.env.API_TARGET ?? `http://localhost:${STUB_PORT}`;
const realApi = Boolean(process.env.API_TARGET);

export default defineConfig({
  testDir: "./e2e",
  // The stub keeps one global state (/sim/reset between tests), so tests must not overlap.
  workers: 1,
  fullyParallel: false,
  retries: 0,
  timeout: realApi ? 240_000 : 30_000,
  expect: { timeout: realApi ? 90_000 : 5_000 },
  reporter: [["list"]],
  use: {
    baseURL: `http://localhost:${UI_PORT}`,
    trace: "retain-on-failure",
    viewport: { width: 1440, height: 900 },
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } }],
  webServer: [
    ...(process.env.API_TARGET
      ? []
      : [
          {
            command: `"${python}" -m uvicorn brain.stub_api.app:app --port ${STUB_PORT}`,
            cwd: root,
            url: `http://localhost:${STUB_PORT}/v1/health`,
            reuseExistingServer: false,
          },
        ]),
    {
      command: `npx vite --mode demo --port ${UI_PORT} --strictPort`,
      url: `http://localhost:${UI_PORT}`,
      env: { API_TARGET: apiTarget },
      reuseExistingServer: false,
    },
  ],
});

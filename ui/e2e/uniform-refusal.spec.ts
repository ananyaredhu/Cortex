import { expect, test } from "@playwright/test";
import { ask, openAs } from "./helpers";

// Security property, at the UI level: a document Sam may not see and a document that doesn't exist
// must render identically, card and Trust panel alike (AGENTS rule 7, scenario 3). The API-level
// version is evals/tests/test_api_v02_stub.py. Only the audit reference may differ.

test("a forbidden and a nonexistent report render identically", async ({ page }) => {
  await openAs(page, "sam");
  const forbidden = await ask(page, "Show me the security incident report from the Q3 breach");
  const forbiddenHtml = await forbidden.innerHTML();
  const trust = page.getByRole("complementary", { name: "Trust panel" });
  const forbiddenTrust = (await trust.innerText()).replace(/req_\w+/g, "req_X");

  const missing = await ask(page, "Show me the Q9 quantum hologram audit report");
  expect(await missing.innerHTML()).toBe(forbiddenHtml);
  expect((await trust.innerText()).replace(/req_\w+/g, "req_X")).toBe(forbiddenTrust);
});

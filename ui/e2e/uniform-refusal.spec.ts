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

test("searching for a forbidden and for a nonexistent report shows the same list", async ({ page }) => {
  await openAs(page, "sam", "/");
  await page.getByRole("button", { name: /^Search/ }).click();
  const dialog = page.getByRole("dialog", { name: "Search" });
  const box = dialog.getByRole("combobox", { name: "Search Cortex" });
  const shown = async (q: string) => {
    await box.fill(q);
    await expect(dialog.getByRole("status")).toHaveText(`Nothing you can open matches “${q}”.`);
    return (await dialog.getByRole("listbox").innerText()).replaceAll(q, "Q");
  };
  const forbidden = await shown("q3 breach security incident report");
  expect(await shown("q9 quantum hologram audit report")).toBe(forbidden);
  expect(forbidden).not.toMatch(/breach report|CANARY/i);
});

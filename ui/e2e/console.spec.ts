import { expect, test, type APIRequestContext } from "@playwright/test";
import { NEEDS_SCRIPTED_EVENTS, openAs } from "./helpers";

// Scenario 5 (audit inquiry) and the Admin page, through the UI. On the real Brain the audit log is persistent,
// so the tests look for the rows they created rather than counting rows.

async function askAs(request: APIRequestContext, persona: string, question: string) {
  const r = await request.post("/v1/ask", {
    headers: { Authorization: `Bearer dev:${persona}` },
    data: { question },
    timeout: 120_000,
  });
  expect(r.ok()).toBeTruthy();
}

test("scenario 5: Jordan reconstructs Priya's PAY history and verifies the chain", async ({ page }) => {
  await openAs(page, "jordan", "/audit");
  await askAs(page.request, "priya", "What's the latest runbook for payment-service incident failover?");
  await askAs(page.request, "priya", "What's the status of the database migration?");

  await page.getByRole("button", { name: "Run query" }).click();
  const rows = page.locator(".audit__results tbody tr");
  const runbook = rows.filter({ hasText: "latest runbook" }).last(); // the question that touched the PAY space
  await expect(runbook).toBeVisible();
  await expect(rows.filter({ hasText: "database migration" })).toHaveCount(0); // the one that did not

  await runbook.click();
  const drawer = page.getByRole("complementary", { name: /Audit entry/ });
  await expect(drawer).toContainText("confluence:PAY/runbook-payment-service");
  await expect(drawer).toContainText("Allowed");

  const chain = page.getByRole("region", { name: "Log integrity" });
  await expect(chain).toContainText("Log integrity verified");
});

test("scenario 5: tampering with one row breaks verification from that row on", async ({ page }) => {
  // Tampering is a demo of the hash chain and alters the log for good, so only on throwaway state.
  test.skip(!(await openAs(page, "jordan", "/audit")), NEEDS_SCRIPTED_EVENTS);
  await askAs(page.request, "priya", "What's the latest runbook for payment-service incident failover?");
  await page.getByRole("button", { name: "Run query" }).click();
  const rows = page.locator(".audit__results tbody tr");
  await expect(rows.first()).toContainText("OK");

  const chain = page.getByRole("region", { name: "Log integrity" });
  await expect(chain).toContainText("Log integrity verified");
  await chain.getByRole("button", { name: /Tamper one row/ }).click();
  await expect(chain).toContainText("does not match its hash");
  await expect(rows.first()).toContainText("Broken");
});

test("the officer gets an answer's hash but not its text when they can't open its sources", async ({ page }) => {
  await openAs(page, "jordan", "/audit");
  await askAs(page.request, "dana", "Show me the security incident report from the Q3 breach");

  await page.getByRole("combobox").first().selectOption("dana@companya.com");
  await page.getByRole("combobox").nth(1).selectOption("");
  await page.getByRole("button", { name: "Run query" }).click();
  await page.locator(".audit__results tbody tr").filter({ hasText: "Q3 breach" }).last().click();

  const drawer = page.getByRole("complementary", { name: /Audit entry/ });
  await expect(drawer).toContainText("Answer text withheld");
  await expect(page.locator("body")).not.toContainText("CANARY");
  await expect(drawer).not.toContainText("admin token");
});

test("admin: connector health, Leak-CI, and the evaluate sandbox", async ({ page }) => {
  await openAs(page, "dana", "/admin");
  for (const name of ["Confluence", "Jira", "Slack", "Drive"]) {
    // "No changes yet" is the healthy chip when the source had no edits in the report's window (real data, idle).
    await expect(page.getByRole("article", { name: `${name} connector` })).toContainText(/Within SLA|No changes yet/);
  }
  await expect(page.getByText(/leaks in \d+ cases/)).toBeVisible();

  await page.getByPlaceholder("e.g. confluence:SEC/q3-breach-report").fill("confluence:SEC/q3-breach-report");
  await page.getByRole("button", { name: "Evaluate" }).click(); // default user: Sam
  await expect(page.getByRole("status").filter({ hasText: /Allowed|Denied/ })).toContainText("Denied");
});

test("people without the roles never see the console screens", async ({ page }) => {
  await openAs(page, "priya", "/audit");
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("link", { name: "Audit console" })).toHaveCount(0);
  await page.goto("/admin");
  await expect(page).toHaveURL(/\/$/);
});

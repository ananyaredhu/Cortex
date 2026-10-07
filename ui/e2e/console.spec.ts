import { expect, test, type APIRequestContext } from "@playwright/test";
import { openAs } from "./helpers";

// Scenario 5 (audit inquiry) and the Admin page, through the UI.

async function askAs(request: APIRequestContext, persona: string, question: string) {
  const r = await request.post("/v1/ask", {
    headers: { Authorization: `Bearer dev:${persona}` },
    data: { question },
  });
  expect(r.ok()).toBeTruthy();
}

test("scenario 5: Jordan reconstructs Priya's PAY history, verifies the chain, and sees tampering", async ({ page }) => {
  await openAs(page, "jordan", "/audit");
  await askAs(page.request, "priya", "What's the latest runbook for payment-service incident failover?");
  await askAs(page.request, "priya", "What's the status of the database migration?");

  await page.getByRole("button", { name: "Run query" }).click();
  const rows = page.locator(".audit__results tbody tr");
  await expect(rows).toHaveCount(1); // only the question that touched the PAY space
  await expect(rows.first()).toContainText("latest runbook");

  await rows.first().click();
  const drawer = page.getByRole("complementary", { name: /Audit entry/ });
  await expect(drawer).toContainText("confluence:PAY/runbook-payment-service");
  await expect(drawer).toContainText("Allowed");

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
  await page.locator(".audit__results tbody tr").filter({ hasText: "Q3 breach" }).click();

  const drawer = page.getByRole("complementary", { name: /Audit entry/ });
  await expect(drawer).toContainText("Answer text withheld");
  await expect(page.locator("body")).not.toContainText("CANARY");
  await expect(drawer).not.toContainText("admin token");
});

test("admin: connector health, Leak-CI, and the evaluate sandbox", async ({ page }) => {
  await openAs(page, "dana", "/admin");
  for (const name of ["Confluence", "Jira", "Slack", "Drive"]) {
    await expect(page.getByRole("article", { name: `${name} connector` })).toContainText("Within SLA");
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

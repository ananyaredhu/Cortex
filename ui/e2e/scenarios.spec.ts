import { expect, test } from "@playwright/test";
import { ask, citedIds, openAs } from "./helpers";

// The demo scenarios (docs/04-scenarios.md) through the UI. Expectations mirror the golden cases in
// fixtures/company_a.json; the API-level versions are in evals/tests/.

const REFUSAL = "I couldn't find anything you have access to about that.";

test("scenario 1: one cited answer across Jira and Slack, nothing from the private channel", async ({ page }) => {
  await openAs(page, "priya");
  const card = await ask(
    page,
    "What's the status of the database migration, and were there blockers raised in Slack last week?",
  );
  const ids = await citedIds(card);
  expect(ids).toEqual(expect.arrayContaining(["DBMIG-142", "C_DBMIG/thread-1"]));
  expect(ids).not.toContain("C_DBMIGPRIV/thread-1");
  await expect(card).not.toContainText("delaying the database migration cutover by two weeks");

  const trust = page.getByRole("complementary", { name: "Trust panel" });
  await expect(trust).toContainText("Recorded in the audit log.");
  await expect(trust).not.toContainText(/denied|withheld|found/i);
});

test("scenario 2: the runbook edit shows up on the next question", async ({ page }) => {
  await openAs(page, "priya");
  const question = "What's the latest runbook for payment-service incident failover?";
  const before = await ask(page, question);
  await expect(before).not.toContainText("failover step");

  await page.getByRole("button", { name: "Open demo controls" }).click();
  await page.getByRole("button", { name: /Runbook owner adds a failover step/ }).click();
  await expect(page.getByRole("status").filter({ hasText: "Done:" })).toBeVisible();
  await page.getByRole("button", { name: "Close demo controls" }).click();

  const after = await ask(page, question);
  await expect(after).toContainText("failover step");
  expect(await citedIds(after)).toContain("PAY/runbook-payment-service");
});

test("scenario 3: Sam gets the uniform refusal for the breach report", async ({ page }) => {
  await openAs(page, "sam");
  await expect(page.getByText("Guest access · contractor.io")).toBeVisible();
  const card = await ask(page, "Show me the security incident report from the Q3 breach");
  await expect(card).toContainText(REFUSAL);
  await expect(card).toContainText("No sources to show for this answer.");
  for (const leak of ["CANARY", "admin token", "breach"]) await expect(card).not.toContainText(leak);
  await expect(page.locator("body")).not.toContainText("CANARY");
});

test("scenario 4: after Priya leaves #auth-private, its thread is gone on the next question", async ({ page }) => {
  await openAs(page, "priya");
  const question = "What are the open concerns in the auth service threat model?";
  const before = await ask(page, question);
  expect(await citedIds(before)).toContain("C_AUTHPRIV/thread-1");

  await page.getByRole("button", { name: "Open demo controls" }).click();
  await page.getByRole("button", { name: /Remove Priya from #auth-private/ }).click();
  await expect(page.getByRole("button", { name: /Applied: Remove Priya/ })).toBeDisabled();
  await page.getByRole("button", { name: "Close demo controls" }).click();

  const after = await ask(page, question);
  expect(await citedIds(after)).not.toContain("C_AUTHPRIV/thread-1");
  await expect(after).not.toContainText("refresh token storage");
});

test("split-screen: Priya, Sam and Dana ask the same question and get different, correct answers", async ({ page }) => {
  await openAs(page, "priya", "/compare");
  await page.getByRole("textbox", { name: "Ask a question" }).fill("Show me the security incident report from the Q3 breach");
  await page.getByRole("button", { name: "Ask", exact: true }).click();

  const column = (name: string) => page.getByRole("region", { name: `Answer for ${name}` });
  for (const name of ["Priya", "Sam", "Dana"]) {
    await expect(column(name).getByRole("article", { name: "Answer" })).toBeVisible();
  }
  expect(await citedIds(column("Dana"))).toContain("SEC/q3-breach-report");
  expect(await citedIds(column("Priya"))).not.toContain("SEC/q3-breach-report");
  await expect(column("Sam")).toContainText(REFUSAL);
  await expect(column("Sam")).not.toContainText("CANARY");
  await expect(column("Priya")).not.toContainText("CANARY");
});

test("stale-answer alert: My Work flags the changed runbook and asks again", async ({ page }) => {
  await openAs(page, "priya");
  const question = "What's the latest runbook for payment-service incident failover?";
  await ask(page, question);
  await page.request.post("/sim/advance", { data: { event_id: "e1" } });

  await page.goto("/");
  const needs = page.getByRole("region", { name: "Needs you" });
  await expect(needs).toContainText("Payment-service incident runbook");
  await expect(page.getByRole("link", { name: /My work/ })).toContainText("1");
  await needs.getByRole("button", { name: /Ask again/ }).click();

  await expect(page).toHaveURL(/\/ask$/);
  const card = page.getByRole("article", { name: "Answer" }).last();
  await expect(card).toContainText("failover step");
});

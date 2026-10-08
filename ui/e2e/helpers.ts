import { expect, type Page } from "@playwright/test";
import type { PersonaId } from "../src/auth/personas";

/** Fresh stub state, then open a page as this persona (the switcher remembers the persona in localStorage). */
export async function openAs(page: Page, persona: PersonaId, path = "/ask") {
  const reset = await page.request.post("/sim/reset");
  expect(reset.ok()).toBeTruthy();
  await page.addInitScript((p) => localStorage.setItem("cortex.persona", p), persona);
  await page.goto(path);
}

/** Ask on the Ask screen and wait until the newest answer card is there. Returns that card. */
export async function ask(page: Page, question: string) {
  const before = await page.getByRole("article", { name: "Answer" }).count();
  await page.getByRole("textbox", { name: "Ask a question" }).fill(question);
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await expect(page.getByRole("article", { name: "Answer" })).toHaveCount(before + 1);
  return page.getByRole("article", { name: "Answer" }).last();
}

/** The fixture doc ids of the sources an answer card lists ("Jira · DBMIG-142" → "DBMIG-142"). */
export async function citedIds(card: ReturnType<Page["locator"]>): Promise<string[]> {
  const where = await card.locator(".source__where").allInnerTexts();
  return where.map((w) => w.split("·")[1].trim());
}

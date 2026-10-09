import { expect, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import type { PersonaId } from "../src/auth/personas";

/**
 * Open a page as this persona (the switcher remembers the persona in localStorage).
 * Against the stub or the fixture runtime, `/sim/reset` first gives every test fresh state. The env runtime
 * (the real Brain over Postgres, the simulators, Slack and Drive) has no reset, answers 404, and keeps its
 * history: tests then assert on what they themselves asked, not on counts.
 * Returns whether scripted events (`/sim/advance`, `/sim/reset`) exist, so a test that needs them can skip.
 */
export async function openAs(page: Page, persona: PersonaId, path = "/ask"): Promise<boolean> {
  const reset = await page.request.post("/sim/reset");
  const scripted = reset.status() !== 404;
  if (scripted) expect(reset.ok()).toBeTruthy();
  await page.addInitScript((p) => localStorage.setItem("cortex.persona", p), persona);
  await page.goto(path);
  return scripted;
}

export const NEEDS_SCRIPTED_EVENTS = "scripted events need the stub or the fixture runtime (BRAIN_RUNTIME=fixture)";

/** Ask on the Ask screen and wait until the newest answer card is there. Returns that card. */
export async function ask(page: Page, question: string) {
  const before = await page.getByRole("article", { name: "Answer" }).count();
  await page.getByRole("textbox", { name: "Ask a question" }).fill(question);
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await expect(page.getByRole("article", { name: "Answer" })).toHaveCount(before + 1);
  return page.getByRole("article", { name: "Answer" }).last();
}

/**
 * The fixture doc ids of the sources an answer card lists ("Jira · DBMIG-142" → "DBMIG-142").
 * The real Brain cites what Slack and Drive assigned ("C0C654ZPPB9/1791121167.965749"); Workstream A's
 * gitignored seed manifests map those back to the fixture ids (evals/ids.py does the same for the golden runner).
 */
export async function citedIds(card: ReturnType<Page["locator"]>): Promise<string[]> {
  const where = await card.locator(".source__where").allInnerTexts();
  return where.map((w) => toFixtureId(w.split("·")[1].trim()));
}

const root = path.resolve(import.meta.dirname, "..", "..");
let realToFixture: Map<string, string> | null = null;

function manifest(file: string): Record<string, Record<string, string>> | null {
  const p = path.join(root, "connectors", file);
  return fs.existsSync(p) ? JSON.parse(fs.readFileSync(p, "utf8")) : null;
}

/** Real platform id (without the `source:` prefix) → fixture id, from the seed manifests; identity when unmapped. */
export function toFixtureId(shortId: string): string {
  if (realToFixture === null) {
    realToFixture = new Map();
    const slack = manifest(path.join("slack", "seed-manifest.local.json"));
    for (const [fixture, ts] of Object.entries(slack?.threads ?? {})) {
      const fixtureShort = fixture.split(":")[1]; // "C_DBMIG/thread-1"
      const channel = slack?.channels?.[fixtureShort.split("/")[0]];
      if (channel) realToFixture.set(`${channel}/${ts}`, fixtureShort);
    }
    const gdrive = manifest(path.join("gdrive", "seed-manifest.local.json"));
    for (const [fixture, fileId] of Object.entries(gdrive?.files ?? {})) {
      realToFixture.set(fileId, fixture.split(":")[1]);
    }
  }
  return realToFixture.get(shortId) ?? shortId;
}

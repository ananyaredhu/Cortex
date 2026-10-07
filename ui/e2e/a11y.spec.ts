import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import type { PersonaId } from "../src/auth/personas";
import { ask, openAs } from "./helpers";

// Automated accessibility pass (axe-core, WCAG 2.1 A and AA) on every screen, in light and dark.
// It catches contrast, names, roles and structure; keyboard paths are covered by the tests below it.

async function axe(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  return results.violations.map(
    (v) => `${v.id} (${v.impact}): ${[...new Set(v.nodes.map((n) => `${n.target.join(" ")} ${n.any[0]?.data?.contrastRatio ?? ""}`))].join(" | ")}`,
  );
}

const SCREENS: { name: string; persona: PersonaId; path: string; prepare?: (page: Page) => Promise<unknown> }[] = [
  { name: "My Work", persona: "priya", path: "/" },
  { name: "Ask, empty", persona: "priya", path: "/ask" },
  {
    name: "Ask, answer and Trust panel",
    persona: "priya",
    path: "/ask",
    prepare: (page) => ask(page, "What's the status of the database migration, and were there blockers raised in Slack last week?"),
  },
  { name: "Ask, refusal", persona: "sam", path: "/ask", prepare: (page) => ask(page, "Show me the security incident report from the Q3 breach") },
  { name: "Compare", persona: "priya", path: "/compare" },
  {
    name: "Audit console with results",
    persona: "jordan",
    path: "/audit",
    prepare: async (page) => {
      await page.getByRole("button", { name: "Run query" }).click();
      await expect(page.getByText(/entr(y|ies)|No entries/).first()).toBeVisible();
    },
  },
  { name: "Admin", persona: "dana", path: "/admin", prepare: (page) => expect(page.getByText(/leaks in/)).toBeVisible() },
];

for (const theme of ["light", "dark"] as const) {
  for (const s of SCREENS) {
    test(`a11y ${theme}: ${s.name}`, async ({ page }) => {
      await page.addInitScript((t) => localStorage.setItem("cortex.theme", t), theme);
      await openAs(page, s.persona, s.path);
      await s.prepare?.(page);
      expect(await axe(page)).toEqual([]);
    });
  }
}

test("keyboard: skip link, ask with Ctrl+Enter, and the persona menu", async ({ page }) => {
  await openAs(page, "priya", "/ask");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to content" })).toBeFocused();

  await page.getByRole("textbox", { name: "Ask a question" }).fill("What is the migration status page situation?");
  await page.keyboard.press("Control+Enter");
  await expect(page.getByRole("article", { name: "Answer" })).toBeVisible();

  const toggle = page.getByRole("button", { name: /Viewing as Priya/ });
  await toggle.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("option", { name: /Priya/ })).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await expect(page.getByRole("option", { name: /Sam/ })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(toggle).toBeFocused();
  await expect(page.getByRole("listbox")).toHaveCount(0);
});

test("phone: drawer navigation and the Trust bottom sheet", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openAs(page, "priya", "/ask");
  await expect(page.getByRole("complementary", { name: "Trust panel" })).toHaveCount(0); // closed by default on phones
  const card = await ask(page, "What's the status of the database migration, and were there blockers raised in Slack last week?");
  await card.getByRole("button", { name: "Trust" }).click();
  const sheet = page.getByRole("complementary", { name: "Trust panel" });
  await expect(sheet).toBeVisible();
  await sheet.getByRole("button", { name: "Close trust panel" }).click();

  await page.getByRole("button", { name: "Open menu" }).click();
  await page.getByRole("link", { name: /My work/ }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("button", { name: "Open menu" })).toHaveAttribute("aria-expanded", "false");
});

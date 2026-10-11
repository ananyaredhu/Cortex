import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import type { Team } from "../api/types";
import { PersonaProvider } from "../auth/PersonaContext";
import { TeamPage } from "../team/TeamPage";

const DBMIG: Team = {
  project_id: "DBMIG",
  name: "Database migration",
  goal: "Move the primary database to the new cluster.",
  target_date: "2026-10-30",
  leads: ["Maya"],
  places: ["jira:DBMIG", "slack:C_DBMIG"],
  issues: {
    total: 2,
    done: 1,
    by_status: [
      { status: "Done", count: 1 },
      { status: "In Progress", count: 1 },
    ],
    items: [
      { doc_id: "jira:DBMIG-142", title: "DBMIG-142 Cutover blocked on replica lag", status: "In Progress", assignee: "Maya", mine: false, url: "https://x/142" },
      { doc_id: "jira:DBMIG-150", title: "DBMIG-150 Tune replication", status: "Done", assignee: "Priya", mine: true, url: "https://x/150" },
    ],
  },
  updates: [
    { doc_id: "slack:C_DBMIG/thread-1", title: "#db-migration thread: blockers", source: "slack", place: "slack:C_DBMIG", author: "Priya", updated_at: "2026-10-10T08:00:00Z", url: "https://x/t1" },
  ],
};

const renderWith = (teams: Team[]) => {
  vi.stubGlobal("fetch", vi.fn().mockImplementation(async () => new Response(JSON.stringify({ teams }))));
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <PersonaProvider>
        <MemoryRouter>
          <TeamPage />
        </MemoryRouter>
      </PersonaProvider>
    </QueryClientProvider>,
  );
};

beforeEach(() => localStorage.clear());

it("shows a team with only the numbers the API returned", async () => {
  renderWith([DBMIG]);
  const card = await screen.findByRole("region", { name: "Database migration" });
  expect(within(card).getByText("Lead Maya")).toBeInTheDocument();
  expect(within(card).getByText("Target 30 Oct")).toBeInTheDocument();
  expect(within(card).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "1");
  expect(within(card).getByRole("progressbar")).toHaveAttribute("aria-valuemax", "2");
  expect(within(card).getByRole("link", { name: /DBMIG-142/ })).toHaveAttribute("href", "https://x/142");
  // one column per status, in board order, with empty ones kept; the caller's own ticket is marked
  const cols = within(card).getAllByRole("region").map((c) => c.getAttribute("aria-label"));
  expect(cols).toEqual(["To Do", "In Progress", "In Review", "Done"].map((s) => `Database migration: ${s}`));
  const done = within(card).getByRole("region", { name: "Database migration: Done" });
  expect(within(done).getByRole("link", { name: /Tune replication/ })).toHaveTextContent("Yours");
  expect(within(within(card).getByRole("region", { name: "Database migration: In Progress" })).queryByText("Yours")).not.toBeInTheDocument();
  expect(within(card).getByRole("link", { name: "#db-migration thread: blockers" })).toBeInTheDocument();
});

it("says the same thing to anyone with no team", async () => {
  renderWith([]);
  expect(await screen.findByText("You are not part of a project yet.")).toBeInTheDocument();
  expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
});

it("hides the progress bar when the person can open no ticket", async () => {
  renderWith([{ ...DBMIG, issues: { total: 0, done: 0, by_status: [], items: [] } }]);
  const card = await screen.findByRole("region", { name: "Database migration" });
  expect(within(card).queryByRole("progressbar")).not.toBeInTheDocument();
  expect(within(card).getByText("No tickets you can see.")).toBeInTheDocument();
});

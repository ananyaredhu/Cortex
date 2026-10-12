import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import type { Team } from "../api/types";
import { PersonaProvider } from "../auth/PersonaContext";
import { at, span, sprintDay, workdays } from "../team/dates";
import { TeamPage } from "../team/TeamPage";

const DBMIG: Team = {
  project_id: "DBMIG",
  name: "Database migration",
  goal: "Move the primary database to the new cluster.",
  start_date: "2026-09-14",
  target_date: "2026-10-30",
  milestone: { label: "Cutover", date: "2026-10-27" },
  sprint: { name: "Sprint 42", start_date: "2026-10-05", end_date: "2026-10-16", goal: "Clear the blocker.", total: 2, done: 1 },
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
          <TeamPage today="2026-10-11" />
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
  // the sprint strip: a Sunday counts as the Friday before it, and the bar is the sprint's own count
  expect(within(card).getByText("Sprint 42 · 5 Oct – 16 Oct · day 5 of 10")).toBeInTheDocument();
  expect(within(card).getByText("Clear the blocker.")).toBeInTheDocument();
  expect(within(card).getByRole("progressbar", { name: "Database migration: sprint tickets done" })).toHaveAttribute("aria-valuenow", "1");
  expect(within(card).getByText("1 of 2")).toBeInTheDocument();
  expect(screen.getByRole("img", { name: "Database migration: 14 Sep to 30 Oct, 1 of 2 tickets done, Cutover on 27 Oct" })).toBeInTheDocument();
  expect(within(card).getByRole("link", { name: "Cutover blocked on replica lag" })).toHaveAttribute("href", "https://x/142");
  // one column per status, in board order, with empty ones kept; the caller's own ticket is marked
  const cols = within(card).getAllByRole("region").map((c) => c.getAttribute("aria-label"));
  expect(cols).toEqual(["To Do", "In Progress", "In Review", "Done"].map((s) => `Database migration: ${s}`));
  const done = within(card).getByRole("region", { name: "Database migration: Done" });
  expect(within(done).getByRole("listitem")).toHaveTextContent(/Tune replication.*DBMIG-150.*Yours/);
  expect(within(within(card).getByRole("region", { name: "Database migration: In Progress" })).queryByText("Yours")).not.toBeInTheDocument();
  expect(within(card).getByRole("link", { name: "#db-migration thread: blockers" })).toBeInTheDocument();
});

// -- moving one's own ticket (api.md 0.3) ---------------------------------------------------------------------------
const MOVABLE: Team = {
  ...DBMIG,
  issues: {
    ...DBMIG.issues,
    items: [
      DBMIG.issues.items[0],
      { ...DBMIG.issues.items[1], status: "In Progress", can_move: true },
    ],
  },
};

/** Serves /v1/team from `teams`, and answers POST /v1/issues/move with `move`, recording what was asked. */
const serve = (move: () => Response) => {
  const asked: unknown[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      if (url === "/v1/issues/move") {
        asked.push(JSON.parse(String(init?.body)));
        return move();
      }
      return new Response(JSON.stringify({ teams: [MOVABLE] }));
    }),
  );
  render(
    <QueryClientProvider client={new QueryClient()}>
      <PersonaProvider>
        <MemoryRouter>
          <TeamPage today="2026-10-11" />
        </MemoryRouter>
      </PersonaProvider>
    </QueryClientProvider>,
  );
  return asked;
};
const column = (name: string) => screen.getByRole("region", { name: `Database migration: ${name}` });

it("lets only the assignee's own ticket be moved, and asks the API to move it", async () => {
  const asked = serve(() => new Response(JSON.stringify({ request_id: "m1", doc_id: "jira:DBMIG-150", status: "Done", updated_at: null, indexed: true })));
  await screen.findByRole("region", { name: "Database migration" });
  const cards = within(column("In Progress")).getAllByRole("listitem");
  expect(cards.map((c) => c.getAttribute("draggable"))).toEqual(["false", "true"]); // Maya's, then the caller's own
  expect(screen.queryByRole("combobox", { name: "Move DBMIG-142 to" })).not.toBeInTheDocument();

  await userEvent.selectOptions(screen.getByRole("combobox", { name: "Move DBMIG-150 to" }), "Done");
  expect(asked).toEqual([{ doc_id: "jira:DBMIG-150", status: "Done" }]);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("moves a card dropped on another column, and never one that is not the caller's", async () => {
  const asked = serve(() => new Response(JSON.stringify({ request_id: "m1", doc_id: "jira:DBMIG-150", status: "In Review", updated_at: null, indexed: true })));
  await screen.findByRole("region", { name: "Database migration" });
  const drop = (docId: string) => {
    const dataTransfer = { types: ["application/x-cortex-issue"], getData: () => docId };
    fireEvent.drop(column("In Review"), { dataTransfer });
  };
  drop("jira:DBMIG-142");
  expect(asked).toEqual([]);
  drop("jira:DBMIG-150");
  await waitFor(() => expect(asked).toEqual([{ doc_id: "jira:DBMIG-150", status: "In Review" }]));
});

it("puts the card back and says why when the move is refused", async () => {
  serve(() => new Response(JSON.stringify({ error: { code: "transition_refused", message: "Jira does not allow that move." } }), { status: 409 }));
  await screen.findByRole("region", { name: "Database migration" });
  await userEvent.selectOptions(screen.getByRole("combobox", { name: "Move DBMIG-150 to" }), "Done");
  expect(await screen.findByRole("alert")).toHaveTextContent("Jira doesn’t allow that move");
  expect(within(column("In Progress")).getByText("Tune replication")).toBeInTheDocument();
  expect(within(column("Done")).queryByText("Tune replication")).not.toBeInTheDocument();
});

it("shows a project's details when its timeline row is pointed at or focused", async () => {
  renderWith([DBMIG]);
  const bar = await screen.findByRole("img", { name: /^Database migration: 14 Sep/ });
  expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  fireEvent.focus(bar);
  const tip = screen.getByRole("tooltip");
  expect(tip).toHaveTextContent("Move the primary database to the new cluster.");
  expect(tip).toHaveTextContent("14 Sep – 30 Oct");
  expect(tip).toHaveTextContent("19 days left");
  expect(tip).toHaveTextContent("Cutover · 27 Oct");
  expect(tip).toHaveTextContent("1 of 2 done");
  expect(tip).toHaveTextContent("Sprint 42 · day 5 of 10 · 1/2 done");
  fireEvent.blur(bar);
  expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  fireEvent.mouseMove(bar);
  expect(screen.getByRole("tooltip")).toBeInTheDocument();
  // the legend says what the dark fill is
  expect(screen.getByRole("list", { name: "How to read the timeline" })).toHaveTextContent("Tickets done, of those you can open");
});

it("says the same thing to anyone with no team", async () => {
  renderWith([]);
  expect(await screen.findByText("You are not part of a project yet.")).toBeInTheDocument();
  expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
});

it("hides the progress bar when the person can open no ticket", async () => {
  renderWith([{ ...DBMIG, issues: { total: 0, done: 0, by_status: [], items: [] }, sprint: { ...DBMIG.sprint!, total: 0, done: 0 } }]);
  const card = await screen.findByRole("region", { name: "Database migration" });
  expect(within(card).queryByRole("progressbar")).not.toBeInTheDocument();
  expect(within(card).getByText("No tickets you can see.")).toBeInTheDocument();
  expect(within(card).getByText("Clear the blocker.")).toBeInTheDocument(); // the sprint's goal, without numbers
  expect(screen.getByRole("img", { name: "Database migration: 14 Sep to 30 Oct, Cutover on 27 Oct" })).toBeInTheDocument();
});

it("leaves out the timeline and the sprint when the registry has no dates", async () => {
  renderWith([{ ...DBMIG, start_date: null, milestone: null, sprint: null }]);
  await screen.findByRole("region", { name: "Database migration" });
  expect(screen.queryByText("Timeline")).not.toBeInTheDocument();
  expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
});

it("does the date arithmetic in whole days", () => {
  const sprint = { start_date: "2026-10-05", end_date: "2026-10-16" };
  expect(workdays("2026-10-05", "2026-10-16")).toBe(10);
  expect(sprintDay(sprint, "2026-10-04")).toEqual({ state: "before", day: 0, of: 10 });
  expect(sprintDay(sprint, "2026-10-05")).toEqual({ state: "during", day: 1, of: 10 });
  expect(sprintDay(sprint, "2026-10-17")).toEqual({ state: "after", day: 10, of: 10 });
  const s = span(["2026-09-16", "2026-10-30"], "2026-10-11"); // a Wednesday start: the span begins on Monday 14 Sep
  expect(s.weeks).toHaveLength(13);
  expect(s.weeks.slice(0, 4).map((w) => w.label)).toEqual(["14 Sep", "21", "28", "5 Oct"]);
  expect(s.weeks.map((w) => w.current).indexOf(true)).toBe(3);
  expect(at(s, "2026-09-14")).toBe(0);
  expect(at(s, "2026-09-21")).toBeCloseTo(100 / 13);
});

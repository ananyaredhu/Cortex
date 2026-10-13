import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import type { MyWork, Team } from "../api/types";
import { PersonaProvider } from "../auth/PersonaContext";
import { HomePage } from "../home/HomePage";

const issue = (key: string, title: string) => ({ doc_id: `jira:${key}`, title: `${key} ${title}`, status: null, url: `https://x/${key}` });
const WORK: MyWork = {
  user: { display_name: "Priya", roles: ["engineer"] },
  issues: [
    issue("PAYINC-9", "Root cause"),
    issue("PAYINC-10", "Circuit breaker"),
    issue("PAYINC-11", "Pool alerts"),
    issue("PAYINC-12", "Retries"),
    issue("PAYINC-13", "Dashboard"),
    issue("DBMIG-142", "Cutover blocked"),
    issue("DBMIG-150", "Tune replication"),
  ],
  projects: ["jira:DBMIG", "jira:PAYINC"],
  channels: ["slack:C_DBMIG"],
  channel_names: { "slack:C_DBMIG": "#db-migration" },
  recent_pages: [{ doc_id: "confluence:PAY/runbook", title: "Payment runbook", updated_at: "2026-10-10T09:00:00Z", url: "https://x/runbook" }],
  suggested_questions: ["What is blocking cutover?"],
  alerts: [],
};
const TEAM = {
  project_id: "DBMIG",
  name: "Database migration",
  places: ["jira:DBMIG", "slack:C_DBMIG"],
  issues: {
    total: 2,
    done: 0,
    by_status: [],
    items: [{ doc_id: "jira:DBMIG-150", title: "DBMIG-150 Tune replication", status: "To Do", assignee: "Priya", mine: true, url: "https://x/DBMIG-150" }],
  },
  updates: [
    { doc_id: "slack:C_DBMIG/thread-1", title: "Blockers thread", source: "slack", place: "slack:C_DBMIG", author: "Maya", updated_at: "2026-10-10T08:00:00Z", url: "https://x/t1" },
  ],
} as unknown as Team;

const renderHome = (work: MyWork = WORK) => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(async (url: string) => new Response(JSON.stringify(url.endsWith("/v1/team") ? { teams: [TEAM] } : work))),
  );
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <PersonaProvider>
        <MemoryRouter>
          <HomePage />
        </MemoryRouter>
      </PersonaProvider>
    </QueryClientProvider>,
  );
};
const section = (name: string) => screen.findByRole("region", { name: new RegExp(name) });

beforeEach(() => localStorage.clear());

it("shows five tickets, the person's own first, until asked for all", async () => {
  renderHome();
  const tickets = await section("My tickets");
  const rows = await within(tickets).findAllByRole("listitem");
  expect(rows).toHaveLength(5);
  await within(tickets).findByText("Yours");
  // the row sets the key apart from the title and says whose the ticket is and where it stands
  const first = within(tickets).getAllByRole("listitem")[0];
  expect(within(first).getByRole("link", { name: "Tune replication" })).toHaveAttribute("href", "https://x/DBMIG-150");
  expect(first).toHaveTextContent("DBMIG-150");
  expect(first).toHaveTextContent("To Do");
  expect(first).toHaveTextContent("Priya");
  await userEvent.click(within(tickets).getByRole("button", { name: "Show all 7" }));
  expect(within(tickets).getAllByRole("listitem")).toHaveLength(7);
});

const addFilter = async (property: string) => {
  await userEvent.click(screen.getByRole("button", { name: "Add filter" }));
  await userEvent.click(screen.getByRole("button", { name: new RegExp(`^${property}`) }));
};

it("filters every list by project or channel, and combines filters", async () => {
  renderHome();
  const tickets = await section("My tickets");
  await within(tickets).findByText("Yours");
  await addFilter("Project or channel");
  await userEvent.click(screen.getByRole("checkbox", { name: "#db-migration" }));
  expect(within(tickets).getByText("No tickets match these filters.")).toBeInTheDocument();
  const recent = await section("Recent pages and threads");
  expect(within(recent).getByText(/Blockers thread/)).toBeInTheDocument();
  expect(within(recent).queryByText(/Payment runbook/)).not.toBeInTheDocument();

  // a second value widens the same filter; a second filter narrows the page
  await userEvent.click(screen.getByRole("checkbox", { name: "DBMIG" }));
  expect(screen.getByRole("button", { name: "Project or channel: #db-migration, DBMIG" })).toBeInTheDocument();
  expect(within(tickets).getAllByRole("listitem")).toHaveLength(2);
  await addFilter("Status");
  await userEvent.click(screen.getByRole("checkbox", { name: "To Do" }));
  expect(within(tickets).getAllByRole("listitem")).toHaveLength(1);
  expect(within(recent).getByText(/Blockers thread/)).toBeInTheDocument(); // a thread has no status: the filter leaves it alone

  await userEvent.click(screen.getByRole("button", { name: "Clear all" }));
  expect(within(recent).getByText(/Payment runbook/)).toBeInTheDocument();
  expect(within(tickets).getAllByRole("listitem")).toHaveLength(5);
});

it("filters tickets to the person's own", async () => {
  renderHome();
  const tickets = await section("My tickets");
  await within(tickets).findByText("Yours");
  await addFilter("Assignee");
  await userEvent.click(screen.getByRole("checkbox", { name: "Me" }));
  expect(within(tickets).getAllByRole("listitem")).toHaveLength(1);
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
});

it("shows a ticket's deadline and priority, and filters by them", async () => {
  // 0.3: /mywork carries the fields itself. One ticket is long overdue, one is far off, the rest have no deadline.
  const dated: MyWork = {
    ...WORK,
    issues: WORK.issues.map((i) =>
      i.doc_id === "jira:PAYINC-9"
        ? { ...i, status: "To Do", due_date: "2020-01-06", priority: "High" }
        : i.doc_id === "jira:PAYINC-10"
          ? { ...i, status: "Done", due_date: "2020-01-07", priority: "Low" }
          : i.doc_id === "jira:PAYINC-11"
            ? { ...i, due_date: "2999-01-01" }
            : i,
    ),
  };
  renderHome(dated);
  const tickets = await section("My tickets");
  expect(await within(tickets).findByText(/Overdue · due 6 Jan/)).toBeInTheDocument();
  expect(within(tickets).getByText("High")).toBeInTheDocument();
  expect(within(tickets).getByText(/^Due 7 Jan/)).toBeInTheDocument(); // finished, so not overdue

  await addFilter("Deadline");
  await userEvent.click(screen.getByRole("checkbox", { name: "Overdue" }));
  expect(within(tickets).getAllByRole("listitem")).toHaveLength(1);
  expect(within(tickets).getByRole("link", { name: "Root cause" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("checkbox", { name: "Due later" }));
  expect(within(tickets).getAllByRole("listitem")).toHaveLength(3);
  await userEvent.click(screen.getByRole("button", { name: "Clear all" }));
  await addFilter("Priority");
  await userEvent.click(screen.getByRole("checkbox", { name: "Low" }));
  expect(within(tickets).getAllByRole("listitem")).toHaveLength(1);
});

it("offers no deadline or priority filter while no ticket has one", async () => {
  renderHome();
  await section("My tickets");
  await userEvent.click(screen.getByRole("button", { name: "Add filter" }));
  expect(screen.queryByRole("button", { name: /^Deadline/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /^Priority/ })).not.toBeInTheDocument();
});

it("is one column with no changed-answers box and no questions box", async () => {
  renderHome({ ...WORK, alerts: [{ request_id: "r1", changed_doc: "confluence:PAY/runbook", changed_at: "2026-10-10T09:00:00Z", summary: "", question: "Q?" }] });
  await section("My tickets");
  expect(screen.queryByText("Needs you")).not.toBeInTheDocument();
  expect(screen.queryByText("Ask Cortex about your work")).not.toBeInTheDocument();
  expect(screen.queryByText("What is blocking cutover?")).not.toBeInTheDocument();
});

it("pins a row to Current focus and keeps only its id", async () => {
  renderHome();
  const focus = await section("Current focus");
  await userEvent.click(await screen.findByRole("button", { name: "Add Payment runbook to Current focus" }));
  expect(within(focus).getByRole("link", { name: "Payment runbook" })).toHaveAttribute("href", "https://x/runbook");
  expect(localStorage.getItem("cortex.focus.priya")).toBe('["confluence:PAY/runbook"]');
  await userEvent.click(within(focus).getByRole("button", { name: "Remove Payment runbook from Current focus" }));
  expect(within(focus).queryByRole("link")).not.toBeInTheDocument();
});

it("draws nothing for a pinned document the API no longer returns", async () => {
  localStorage.setItem("cortex.focus.priya", '["confluence:PAY/runbook","jira:DBMIG-142"]');
  renderHome({ ...WORK, recent_pages: [] });
  const focus = await section("Current focus");
  expect(await within(focus).findByRole("link", { name: /DBMIG-142/ })).toBeInTheDocument();
  expect(within(focus).getAllByRole("listitem")).toHaveLength(1);
  expect(focus).not.toHaveTextContent(/runbook/i);
});

it("draws a source pinned from an answer that is in none of My Work's lists, and nothing for one that can't be opened", async () => {
  localStorage.setItem("cortex.focus.priya", '["slack:D1/1.2","confluence:SEC/breach"]');
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(async (url: string) => {
      const found = { found: true, doc_id: "slack:D1/1.2", title: "DM dana, priya thread", url: "https://x/dm", source: "slack", as_of: null, text: "", truncated: false, why_visible: [], flags: [] };
      const body = url.includes("/v1/source?") ? (url.includes("slack") ? found : { found: false }) : url.endsWith("/v1/team") ? { teams: [TEAM] } : WORK;
      return new Response(JSON.stringify(body));
    }),
  );
  render(
    <QueryClientProvider client={new QueryClient()}>
      <PersonaProvider>
        <MemoryRouter>
          <HomePage />
        </MemoryRouter>
      </PersonaProvider>
    </QueryClientProvider>,
  );
  const focus = await section("Current focus");
  expect(await within(focus).findByRole("link", { name: "DM dana, priya thread" })).toBeInTheDocument();
  expect(within(focus).getAllByRole("listitem")).toHaveLength(1);
});

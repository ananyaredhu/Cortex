import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import type { SearchHit } from "../api/types";
import { PersonaProvider } from "../auth/PersonaContext";
import { PERSONAS } from "../auth/personas";
import { Sidebar } from "../components/Sidebar";
import { buildGroups, localItems, matches } from "../search/items";
import { SearchProvider } from "../search/SearchPalette";
import { SourceViewerProvider } from "../source/SourceViewer";

const WORK = {
  user: { display_name: "Priya", roles: ["engineer"] },
  issues: [{ doc_id: "jira:DBMIG-142", title: "DBMIG-142 Cutover blocked on replica lag", status: "In Progress", url: "http://localhost:8102/browse/DBMIG-142", mine: true }],
  projects: ["jira:DBMIG"],
  channels: ["slack:C_DBMIG"],
  recent_pages: [{ doc_id: "confluence:PAY/runbook-payment-service", title: "Runbook: payment-service failover", updated_at: "2026-10-09T10:00:00Z", url: "https://wiki.example.com/PAY/runbook" }],
  suggested_questions: [],
  alerts: [],
};
const CHATS = [{ conversation_id: "c_7", title: "Database migration blockers", title_source: "generated", last_asked_at: "2026-10-10T14:05:00Z" }];
const ROUTINES = [
  { routine_id: "rt_1", name: "Morning brief", question: "What changed on the migration?", sources: [], schedule: { frequency: "daily", weekday: null, time: "09:00" }, timezone: "UTC", status: "active", paused_reason: null, next_run_at: null, last_run: null },
];
const HIT: SearchHit = {
  doc_id: "slack:C_DBMIG/thread-1",
  title: "#db-migration thread: blockers last week",
  url: "https://fixtures.invalid/slack/C_DBMIG/thread-1",
  source: "slack",
  as_of: "2026-10-09T16:00:00Z",
  snippet: "Replica lag is the main blocker for cutover.",
  why_visible: ["user:priya@companya.com", "channel:C_DBMIG"],
  flags: [],
};
const AGAIN: SearchHit = { ...HIT, doc_id: "jira:DBMIG-142", title: "DBMIG-142 Cutover blocked on replica lag", source: "jira", snippet: "Cutover is blocked: replica lag stays above 5 seconds." };

function Where() {
  const l = useLocation();
  return <output data-testid="where">{l.pathname + JSON.stringify(l.state)}</output>;
}

const setup = (results: SearchHit[] = [HIT, AGAIN]) => {
  const fetchMock = vi.fn().mockImplementation(async (input: string) => {
    const url = String(input);
    const body = url.startsWith("/v1/search")
      ? { results }
      : url.endsWith("/v1/conversations")
        ? { conversations: CHATS }
        : url.endsWith("/v1/mywork")
          ? WORK
          : url.endsWith("/v1/team")
            ? { teams: [] }
            : url.endsWith("/v1/routines")
              ? { routines: ROUTINES }
              : { found: false };
    return new Response(JSON.stringify(body));
  });
  vi.stubGlobal("fetch", fetchMock);
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <PersonaProvider>
        <MemoryRouter>
          <SourceViewerProvider>
            <SearchProvider>
              <Sidebar />
              <Where />
            </SearchProvider>
          </SourceViewerProvider>
        </MemoryRouter>
      </PersonaProvider>
    </QueryClientProvider>,
  );
  return { searches: () => fetchMock.mock.calls.map((c) => String(c[0])).filter((u) => u.startsWith("/v1/search")) };
};

const openSearch = async () => {
  await userEvent.click(screen.getByRole("button", { name: /Search/ }));
  return screen.findByRole("dialog", { name: "Search" });
};

beforeEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

it("sits below Ask in the sidebar and opens with a click or Ctrl+K", async () => {
  setup();
  const nav = screen.getByRole("navigation", { name: "Main" });
  expect([...nav.children].slice(0, 2).map((el) => el.textContent?.trim().replace(/(Ctrl K|⌘K)$/, ""))).toEqual(["Ask", "Search"]);
  const dialog = await openSearch();
  expect(within(dialog).getByRole("combobox", { name: "Search Cortex" })).toHaveFocus();
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Search/ })).toHaveFocus();
  await userEvent.keyboard("{Control>}k{/Control}");
  expect(await screen.findByRole("dialog", { name: "Search" })).toBeInTheDocument();
});

it("lists the person's recent things before anything is typed, and asks the Brain nothing", async () => {
  const { searches } = setup();
  const dialog = await openSearch();
  expect(await within(dialog).findByRole("option", { name: /Database migration blockers/ })).toBeInTheDocument();
  expect(within(dialog).getByRole("option", { name: /DBMIG-142 Cutover blocked/ })).toHaveTextContent("In Progress");
  expect(within(dialog).getByRole("option", { name: /Morning brief/ })).toBeInTheDocument();
  expect(within(dialog).getByRole("option", { name: /Team focus/ })).toBeInTheDocument();
  expect(within(dialog).queryByRole("option", { name: /Audit console/ })).not.toBeInTheDocument(); // Priya is not compliance
  expect(searches()).toEqual([]);
});

it("finds a ticket from half its key at once, then adds what the Brain found without repeating it", async () => {
  const { searches } = setup();
  const dialog = await openSearch();
  await within(dialog).findByRole("option", { name: /Morning brief/ });
  await userEvent.type(within(dialog).getByRole("combobox"), "dbmig 14");
  expect(within(dialog).getByRole("option", { name: /DBMIG-142 Cutover blocked/ })).toBeInTheDocument(); // before the Brain answers
  expect(await within(dialog).findByRole("option", { name: /#db-migration thread/ })).toHaveTextContent("Replica lag is the main blocker");
  expect(within(dialog).getAllByRole("option", { name: /DBMIG-142/ })).toHaveLength(1);
  expect(within(dialog).getByRole("option", { name: /DBMIG-142/ })).toHaveTextContent("stays above 5 seconds");
  // asked once, after the pause, not once per key
  expect(searches()).toEqual(["/v1/search?q=dbmig+14&limit=8"]);
});

it("opens a document inside Cortex, a chat on Ask and a routine on its runs", async () => {
  setup();
  let dialog = await openSearch();
  await userEvent.click(await within(dialog).findByRole("option", { name: /DBMIG-142 Cutover blocked/ }));
  expect(await screen.findByRole("dialog", { name: /DBMIG-142 Cutover blocked/ })).toBeInTheDocument(); // the source viewer
  expect(screen.queryByRole("dialog", { name: "Search" })).not.toBeInTheDocument();
  expect(screen.getByTestId("where")).toHaveTextContent("/null");
  await userEvent.keyboard("{Escape}");

  dialog = await openSearch();
  await userEvent.type(within(dialog).getByRole("combobox"), "blockers");
  expect(within(dialog).getByRole("option", { name: /Database migration blockers/ })).toHaveAttribute("aria-selected", "true");
  await userEvent.keyboard("{Enter}");
  expect(screen.getByTestId("where")).toHaveTextContent("/ask/c_7");

  dialog = await openSearch();
  await userEvent.click(await within(dialog).findByRole("option", { name: /Morning brief/ }));
  expect(screen.getByTestId("where")).toHaveTextContent("/routines/rt_1");
});

it("opens the original in a new tab on Ctrl+Enter only when a reader could reach it", async () => {
  const opened = vi.fn();
  setup([]);
  vi.stubGlobal("open", opened);
  const dialog = await openSearch();
  await userEvent.type(within(dialog).getByRole("combobox"), "runbook");
  await within(dialog).findByRole("option", { name: /Runbook: payment-service/ });
  expect(within(dialog).getByText("Open the original")).toBeInTheDocument();
  await userEvent.keyboard("{Control>}{Enter}{/Control}");
  expect(opened).toHaveBeenCalledWith("https://wiki.example.com/PAY/runbook", "_blank", "noopener,noreferrer");

  await userEvent.clear(within(dialog).getByRole("combobox"));
  await userEvent.type(within(dialog).getByRole("combobox"), "dbmig-142"); // its address is on localhost
  expect(within(dialog).queryByText("Open the original")).not.toBeInTheDocument();
});

it("says one thing when nothing matches and offers to ask the question instead", async () => {
  setup([]);
  const dialog = await openSearch();
  await userEvent.type(within(dialog).getByRole("combobox"), "q3 breach report");
  expect(await within(dialog).findByText("Nothing you can open matches “q3 breach report”.")).toBeInTheDocument();
  expect(within(dialog).getAllByRole("option")).toHaveLength(1);
  await userEvent.keyboard("{Enter}");
  expect(screen.getByTestId("where")).toHaveTextContent('/ask{"ask":"q3 breach report"}');
});

it("narrows to one source, and answers the Chats and Routines tabs without the Brain", async () => {
  const { searches } = setup();
  const dialog = await openSearch();
  await userEvent.click(within(dialog).getByRole("button", { name: "Slack" }));
  await userEvent.type(within(dialog).getByRole("combobox"), "blockers");
  expect(await within(dialog).findByRole("option", { name: /#db-migration thread/ })).toBeInTheDocument();
  expect(within(dialog).queryByRole("option", { name: /Database migration blockers/ })).not.toBeInTheDocument();
  expect(searches()).toEqual(["/v1/search?q=blockers&limit=10&sources=slack"]);

  await userEvent.click(within(dialog).getByRole("button", { name: "Routines" }));
  await userEvent.clear(within(dialog).getByRole("combobox"));
  await userEvent.type(within(dialog).getByRole("combobox"), "morning");
  await waitFor(() => expect(within(dialog).getAllByRole("option")).toHaveLength(1));
  await new Promise((r) => setTimeout(r, 400));
  expect(searches()).toHaveLength(1);
});

it("matches every word in any order, and builds a list with no document twice", () => {
  expect(matches("DBMIG-142 Cutover blocked", "cut dbmig")).toBe(true);
  expect(matches("DBMIG-142 Cutover blocked", "cutover payments")).toBe(false);
  const local = localItems({ conversations: CHATS as never, work: WORK as never, routines: ROUTINES as never }, PERSONAS[3]);
  expect(local.filter((i) => i.kind === "page").map((i) => i.title)).toEqual(["New question", "My work", "Team focus", "Routines", "Audit console", "Admin"]);
  const groups = buildGroups(local, [HIT, AGAIN], "replica", "all");
  expect(groups.map((g) => g.kind)).toEqual(["jira", "slack", "ask"]);
  expect(groups.flatMap((g) => g.items).map((i) => i.key)).toEqual(["jira:DBMIG-142", "slack:C_DBMIG/thread-1", "ask"]);
});

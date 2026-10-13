import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import type { ConversationDetail, MyWork, Routine } from "../api/types";
import { AskPage } from "../ask/AskPage";
import { PersonaProvider } from "../auth/PersonaContext";
import { HomePage } from "../home/HomePage";
import { scheduleText, whenIn } from "../routines/format";
import { RoutineHistoryPage } from "../routines/RoutineHistoryPage";
import { RoutinesPage } from "../routines/RoutinesPage";
import { TEMPLATES } from "../routines/templates";

const BRIEF: Routine = {
  routine_id: "rt_1",
  name: "Morning brief",
  question: "Summarise what needs my focus today.",
  sources: [],
  schedule: { frequency: "weekdays", weekday: null, time: "09:00" },
  timezone: "Asia/Singapore",
  status: "active",
  paused_reason: null,
  next_run_at: "2026-10-13T01:00:00Z",
  last_run: { request_id: "req_1", conversation_id: "c_run", ran_at: "2026-10-12T01:00:20Z", outcome: "answered", unread: true },
};

/** Serves the routines list from `state`, and records every other call as [method, path, body]. */
const serve = (state: { routines: Routine[] }, answer: (method: string, url: string) => Response | undefined = () => undefined) => {
  const calls: [string, string, unknown][] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (method !== "GET") calls.push([method, url, init?.body ? JSON.parse(String(init.body)) : null]);
      return answer(method, url) ?? new Response(JSON.stringify(url === "/v1/routines" && method === "GET" ? state : (state.routines[0] ?? {})));
    }),
  );
  return calls;
};

const renderPage = (at = "/routines", ask = <p>opened the run</p>) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <PersonaProvider>
        <MemoryRouter initialEntries={[at]}>
          <Routes>
            <Route path="/routines" element={<RoutinesPage />} />
            <Route path="/routines/:routineId" element={<RoutineHistoryPage />} />
            <Route path="/ask/:conversationId" element={ask} />
          </Routes>
        </MemoryRouter>
      </PersonaProvider>
    </QueryClientProvider>,
  );

beforeEach(() => localStorage.clear());

it("says a schedule and a time in words, in the routine's own time zone", () => {
  expect(scheduleText(BRIEF.schedule)).toBe("Every weekday at 09:00");
  expect(scheduleText({ frequency: "weekly", weekday: 6, time: "18:30" })).toBe("Every Sunday at 18:30");
  expect(scheduleText({ frequency: "daily", weekday: null, time: "07:00" })).toBe("Every day at 07:00");
  expect(whenIn("2026-10-13T01:00:00Z", "Asia/Singapore")).toBe("Tue 13 Oct, 09:00");
  expect(whenIn("2026-10-13T01:00:00Z", "Not/AZone")).toBe("Tue 13 Oct, 01:00 UTC");
  expect(whenIn(null, "UTC")).toBe("");
});

it("lists a routine with its schedule, next run and last outcome, and no answer text", async () => {
  serve({ routines: [BRIEF] });
  renderPage();
  const item = await screen.findByRole("listitem", { name: "Morning brief" });
  expect(item).toHaveTextContent("Summarise what needs my focus today.");
  expect(item).toHaveTextContent("Every weekday at 09:00 (Asia/Singapore) · next Tue 13 Oct, 09:00");
  expect(item).toHaveTextContent("Last run Mon 12 Oct, 09:00: Answered.");
  expect(within(item).getByRole("img", { name: "Unread" })).toBeInTheDocument();
  expect(within(item).getByRole("link", { name: /Open/ })).toHaveAttribute("href", "/ask/c_run");
});

it("creates a routine from fixed fields", async () => {
  const calls = serve({ routines: [] });
  renderPage();
  expect(await screen.findByRole("heading", { name: "You have no routines yet" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Create your first routine" }));
  const form = screen.getByRole("dialog", { name: "New routine" });
  expect(within(form).getByRole("button", { name: "Create routine" })).toBeDisabled();
  await userEvent.type(within(form).getByLabelText("Name"), "Friday wrap-up");
  await userEvent.type(within(form).getByLabelText("Instructions"), "What changed in the migration this week?");
  await userEvent.selectOptions(within(form).getByLabelText("Repeats"), "weekly");
  await userEvent.selectOptions(within(form).getByLabelText("On"), "Friday");
  await userEvent.clear(within(form).getByLabelText("Time zone"));
  await userEvent.type(within(form).getByLabelText("Time zone"), "Asia/Singapore");
  await userEvent.click(within(form).getByRole("checkbox", { name: "Jira" }));
  await userEvent.click(within(form).getByRole("button", { name: "Create routine" }));
  expect(calls).toEqual([
    ["POST", "/v1/routines", {
      name: "Friday wrap-up", question: "What changed in the migration this week?", sources: ["jira"],
      schedule: { frequency: "weekly", weekday: 4, time: "09:00" }, timezone: "Asia/Singapore",
    }],
  ]);
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
}, 20_000); // a dozen typed fields: slow on a busy machine

it("offers templates that fill the form, every field still editable, and returns to the list once saved", async () => {
  const calls = serve({ routines: [] });
  renderPage();
  await userEvent.click(await screen.findByRole("button", { name: "Start from a template" }));
  expect(screen.getByRole("tab", { name: "Templates" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getAllByRole("listitem")).toHaveLength(TEMPLATES.length);
  await userEvent.click(screen.getByRole("button", { name: /Week ahead/ }));
  const form = screen.getByRole("dialog", { name: "New routine" });
  expect(within(form).getByLabelText("Name")).toHaveValue("Week ahead");
  expect((within(form).getByLabelText("Instructions") as HTMLTextAreaElement).value).toMatch(/^Plan my week\.\n1\. /);
  expect(within(form).getByLabelText("Repeats")).toHaveValue("weekly");
  expect(within(form).getByLabelText("On")).toHaveValue("0");
  expect(within(form).getByRole("checkbox", { name: "Jira" })).toBeChecked();
  await userEvent.clear(within(form).getByLabelText("Name"));
  await userEvent.type(within(form).getByLabelText("Name"), "My week");
  await userEvent.click(within(form).getByRole("button", { name: "Create routine" }));
  expect(calls).toHaveLength(1);
  expect(calls[0][2]).toMatchObject({ name: "My week", sources: ["jira"], schedule: { frequency: "weekly", weekday: 0, time: "09:00" } });
  await waitFor(() => expect(screen.getByRole("tab", { name: "Yours" })).toHaveAttribute("aria-selected", "true"));
});

it("keeps every template inside what a routine may be", () => {
  expect(new Set(TEMPLATES.map((t) => t.id)).size).toBe(TEMPLATES.length);
  for (const t of TEMPLATES) {
    expect(t.name.length).toBeLessThanOrEqual(80);
    expect(t.instructions.length).toBeLessThanOrEqual(2000);
    expect(t.schedule.time).toMatch(/^\d\d:\d\d$/);
    expect(t.schedule.weekday === null).toBe(t.schedule.frequency !== "weekly");
  }
});

it("says the Brain has no routines when the list is a 404, not a bare Not Found", async () => {
  serve({ routines: [] }, () => new Response(JSON.stringify({ detail: "Not Found" }), { status: 404 }));
  renderPage();
  expect(await screen.findByText(/This Brain does not serve routines yet/, {}, { timeout: 12_000 })).toBeInTheDocument(); // after the query's retries
  expect(screen.queryByText("Not Found")).not.toBeInTheDocument();
}, 15_000);

it("says so when the limit of five is reached, and keeps the form open", async () => {
  serve({ routines: [] }, (method) => (method === "POST" ? new Response(JSON.stringify({ detail: "at most 5 active routines" }), { status: 409 }) : undefined));
  renderPage();
  await userEvent.click(await screen.findByRole("button", { name: "New routine" }));
  await userEvent.type(screen.getByLabelText("Name"), "Sixth");
  await userEvent.type(screen.getByLabelText("Instructions"), "Anything new?");
  await userEvent.click(screen.getByRole("button", { name: "Create routine" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("You already have five active routines");
  expect(screen.getByRole("dialog")).toBeInTheDocument();
});

it("pauses, deletes only after a second click, and opens the answer of a run started by hand", async () => {
  const calls = serve({ routines: [BRIEF] }, (_method, url) =>
    url.endsWith("/run") ? new Response(JSON.stringify({ request_id: "req_2", conversation_id: "c_new" })) : undefined,
  );
  renderPage();
  const item = await screen.findByRole("listitem", { name: "Morning brief" });
  await userEvent.click(within(item).getByRole("button", { name: /Pause/ }));
  await userEvent.click(within(item).getByRole("button", { name: /Delete/ }));
  expect(calls).toEqual([["PATCH", "/v1/routines/rt_1", { status: "paused" }]]);
  await userEvent.click(within(item).getByRole("button", { name: /Really delete/ }));
  expect(calls[1]).toEqual(["DELETE", "/v1/routines/rt_1", null]);
  await userEvent.click(within(item).getByRole("button", { name: /Run now/ }));
  expect(calls[2]).toEqual(["POST", "/v1/routines/rt_1/run", null]);
  expect(await screen.findByText("opened the run")).toBeInTheDocument();
});

it("explains a pause Cortex made itself", async () => {
  serve({ routines: [{ ...BRIEF, status: "paused", paused_reason: "unopened", next_run_at: null }] });
  renderPage();
  const item = await screen.findByRole("listitem", { name: "Morning brief" });
  expect(item).toHaveTextContent("Paused because nobody had opened it for two weeks.");
  expect(within(item).getByRole("button", { name: /Resume/ })).toBeInTheDocument();
});

it("shows active routines on My Work, the latest run linked and marked unread", async () => {
  const work: MyWork = {
    user: { display_name: "Priya", roles: ["engineer"] }, issues: [], projects: [], channels: [], recent_pages: [],
    suggested_questions: [], alerts: [],
    routines: [BRIEF, { ...BRIEF, routine_id: "rt_2", name: "Paused one", status: "paused" }, { ...BRIEF, routine_id: "rt_3", name: "New one", last_run: null }],
  };
  vi.stubGlobal("fetch", vi.fn().mockImplementation(async (url: string) => new Response(JSON.stringify(url.endsWith("/v1/team") ? { teams: [] } : work))));
  render(
    <QueryClientProvider client={new QueryClient()}>
      <PersonaProvider>
        <MemoryRouter>
          <HomePage />
        </MemoryRouter>
      </PersonaProvider>
    </QueryClientProvider>,
  );
  const block = await screen.findByRole("region", { name: "Routines" });
  expect(within(block).getAllByRole("listitem")).toHaveLength(2);
  expect(within(block).getByRole("link", { name: "Morning brief" })).toHaveAttribute("href", "/ask/c_run");
  expect(within(block).getByText("Answered · Mon 12 Oct, 09:00")).toBeInTheDocument();
  expect(within(block).getByRole("img", { name: "Unread" })).toBeInTheDocument();
  expect(within(block).getByText("Every weekday at 09:00 · first run Tue 13 Oct, 09:00")).toBeInTheDocument();
  expect(within(block).queryByText("Paused one")).not.toBeInTheDocument();
});

it("says a routine is running and does not offer to run it again", async () => {
  serve({ routines: [{ ...BRIEF, running: true }] });
  renderPage();
  const item = await screen.findByRole("listitem", { name: "Morning brief" });
  expect(within(item).getByRole("status")).toHaveTextContent("Running now");
  expect(within(item).getByRole("button", { name: /Running/ })).toBeDisabled();
  expect(within(item).getByRole("link", { name: /History/ })).toHaveAttribute("href", "/routines/rt_1");
});

// A run reopened from the log: its answer, and the trust signals recorded when it was answered (api.md 0.3).
const RUN: ConversationDetail = {
  conversation_id: "c_run", title: "Morning brief · 12 Oct", last_asked_at: "2026-10-12T01:00:20Z",
  turns: [{
    request_id: "req_1", asked_at: "2026-10-12T01:00:20Z", question: BRIEF.question, answer: "Cutover is blocked on replica lag.",
    withheld: false, refused: false, abstained: false, routine_id: "rt_1",
    citations: [{ doc_id: "jira:DBMIG-142", title: "Cutover", url: "https://jira.example/DBMIG-142", source: "jira", as_of: "2026-10-10T13:58:00Z", why_visible: [] }],
    coverage: { confluence: { searched: true, shown: 0 }, jira: { searched: true, shown: 1 }, slack: { searched: true, shown: 0 }, gdrive: { searched: false, shown: 0 } },
    freshness: { oldest_source_as_of: "2026-10-10T13:58:00Z", stale_refetched: 0, per_source: { jira: { last_sync: "2026-10-12T00:58:00Z", status: "ok" } } },
    grounding: { score: 1, removed_claims: 0 }, policy_version: "pol-0.3", unavailable_sources: [],
  }],
};
const HISTORY = {
  runs: [
    { request_id: "rts_2", conversation_id: null, ran_at: "2026-10-13T01:00:10Z", outcome: "skipped", unread: false },
    { request_id: "req_1", conversation_id: "c_run", ran_at: "2026-10-12T01:00:20Z", outcome: "answered", unread: false },
  ],
};
const history = (run: ConversationDetail) => (_method: string, url: string) =>
  url.endsWith("/runs") ? new Response(JSON.stringify(HISTORY)) : url.includes("/v1/conversations/") ? new Response(JSON.stringify(run)) : undefined;

it("shows a routine's runs in one place, the newest answer open beside them", async () => {
  serve({ routines: [BRIEF] }, history(RUN));
  renderPage("/routines/rt_1");
  expect(await screen.findByRole("heading", { name: /Morning brief/ })).toBeInTheDocument();
  const runs = await screen.findByRole("region", { name: /Runs/ });
  expect(within(runs).getAllByRole("listitem")).toHaveLength(2);
  expect(within(runs).getByText("Skipped").closest("button")).toBeNull();       // nothing was asked: nothing to open
  expect(within(runs).getByRole("button", { name: /Mon 12 Oct, 09:00/ })).toHaveAttribute("aria-current", "true");
  const answer = screen.getByRole("region", { name: "Answer of the selected run" });
  expect(await within(answer).findByText("Cutover is blocked on replica lag.")).toBeInTheDocument();
  expect(within(answer).getByRole("link", { name: /Open in Ask/ })).toHaveAttribute("href", "/ask/c_run");
});

it("withholds a past run in the history as Ask would, and treats an unknown routine like someone else's", async () => {
  serve({ routines: [BRIEF] }, history({ ...RUN, turns: [{ ...RUN.turns[0], answer: null, withheld: true, citations: [], coverage: null, freshness: null, grounding: null }] }));
  const first = renderPage("/routines/rt_1");
  expect(await screen.findByText(/This answer isn’t shown again/)).toBeInTheDocument();
  expect(screen.queryByText("Cutover is blocked on replica lag.")).not.toBeInTheDocument();
  first.unmount();
  renderPage("/routines/rt_nope");
  expect(await screen.findByRole("alert")).toHaveTextContent("That routine isn’t available.");
});

it("fills the Trust panel of a reopened run from what was recorded when it ran", async () => {
  serve({ routines: [BRIEF] }, (_method, url) =>
    url.includes("/v1/conversations/") ? new Response(JSON.stringify(RUN)) : url === "/v1/conversations" ? new Response(JSON.stringify({ conversations: [] })) : undefined,
  );
  renderPage("/ask/c_run", <AskPage />);
  const trust = await screen.findByRole("complementary", { name: "Trust panel" });
  const jira = await within(trust).findByRole("row", { name: /Jira/ });
  expect(jira).toHaveTextContent("Yes");
  expect(within(jira).getByRole("cell", { name: "1" })).toBeInTheDocument();
  expect(within(trust).getByRole("row", { name: /Drive/ })).toHaveTextContent("No");
  expect(trust).toHaveTextContent("100%");
  expect(trust).toHaveTextContent("pol-0.3");
  expect(trust).toHaveTextContent("As it was when this was answered, 12 Oct, 01:00 UTC.");
  expect(await screen.findByRole("link", { name: "All runs" })).toHaveAttribute("href", "/routines/rt_1");
});

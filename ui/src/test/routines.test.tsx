import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import type { MyWork, Routine } from "../api/types";
import { PersonaProvider } from "../auth/PersonaContext";
import { HomePage } from "../home/HomePage";
import { scheduleText, whenIn } from "../routines/format";
import { RoutinesPage } from "../routines/RoutinesPage";

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

const renderPage = () =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <PersonaProvider>
        <MemoryRouter initialEntries={["/routines"]}>
          <Routes>
            <Route path="/routines" element={<RoutinesPage />} />
            <Route path="/ask/:conversationId" element={<p>opened the run</p>} />
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
  expect(item).toHaveTextContent("“Summarise what needs my focus today.”");
  expect(item).toHaveTextContent("Every weekday at 09:00 (Asia/Singapore) · next Tue 13 Oct, 09:00");
  expect(item).toHaveTextContent("Last run Mon 12 Oct, 09:00: Answered.");
  expect(within(item).getByRole("img", { name: "Unread" })).toBeInTheDocument();
  expect(within(item).getByRole("link", { name: /Open/ })).toHaveAttribute("href", "/ask/c_run");
});

it("creates a routine from fixed fields", async () => {
  const calls = serve({ routines: [] });
  renderPage();
  expect(await screen.findByText(/No routines yet/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "New routine" }));
  const form = screen.getByRole("dialog", { name: "New routine" });
  expect(within(form).getByRole("button", { name: "Create routine" })).toBeDisabled();
  await userEvent.type(within(form).getByLabelText("Name"), "Friday wrap-up");
  await userEvent.type(within(form).getByLabelText("Question"), "What changed in the migration this week?");
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

it("says so when the limit of five is reached, and keeps the form open", async () => {
  serve({ routines: [] }, (method) => (method === "POST" ? new Response(JSON.stringify({ detail: "at most 5 active routines" }), { status: 409 }) : undefined));
  renderPage();
  await userEvent.click(await screen.findByRole("button", { name: "New routine" }));
  await userEvent.type(screen.getByLabelText("Name"), "Sixth");
  await userEvent.type(screen.getByLabelText("Question"), "Anything new?");
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

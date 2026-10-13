import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import { PersonaProvider } from "../auth/PersonaContext";
import { Sidebar } from "../components/Sidebar";

beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal("fetch", vi.fn().mockImplementation(async () => new Response(JSON.stringify({ conversations: [], alerts: [] }))));
});

const renderSidebar = () =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <PersonaProvider>
        <MemoryRouter>
          <Sidebar />
        </MemoryRouter>
      </PersonaProvider>
    </QueryClientProvider>,
  );

it("lists recent conversations as links that reopen them", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(async (url: string) =>
      new Response(
        JSON.stringify(
          url.endsWith("/v1/conversations")
            ? { conversations: [{ conversation_id: "c_7", title: "What's blocking cutover?", last_asked_at: "2026-10-10T14:05:00Z" }] }
            : { alerts: [] },
        ),
      ),
    ),
  );
  renderSidebar();
  const link = await screen.findByRole("link", { name: "What's blocking cutover?" }, { timeout: 5000 });
  expect(link).toHaveAttribute("href", "/ask/c_7");
});

it("renames a chat, and an emptied name goes back to the generated title", async () => {
  const sent: unknown[] = [];
  let title = "Database migration blockers";
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      if (init?.method === "PATCH") {
        const body = JSON.parse(String(init.body));
        sent.push([url, body]);
        title = body.title ?? "Database migration blockers";
      }
      const c = { conversation_id: "c_7", title, title_source: "generated", last_asked_at: "2026-10-10T14:05:00Z" };
      return new Response(JSON.stringify(url.endsWith("/v1/conversations") ? { conversations: [c] } : c));
    }),
  );
  renderSidebar();
  await userEvent.click(await screen.findByRole("button", { name: "Rename “Database migration blockers”" }));
  const input = screen.getByRole("textbox", { name: "Chat name" });
  await userEvent.clear(input);
  await userEvent.type(input, "  Cutover notes {Enter}");
  expect(sent).toEqual([["/v1/conversations/c_7", { title: "Cutover notes" }]]);
  expect(await screen.findByRole("link", { name: "Cutover notes" })).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "Rename “Cutover notes”" }));
  await userEvent.clear(screen.getByRole("textbox", { name: "Chat name" }));
  await userEvent.keyboard("{Enter}");
  await waitFor(() => expect(sent[1]).toEqual(["/v1/conversations/c_7", { title: null }]));

  // Escape leaves the name alone
  await userEvent.click(await screen.findByRole("button", { name: "Rename “Database migration blockers”" }));
  await userEvent.type(screen.getByRole("textbox", { name: "Chat name" }), "x{Escape}");
  expect(sent).toHaveLength(2);
});

it("shows console links only to the roles that may use them", async () => {
  renderSidebar();
  expect(screen.queryByText("Audit console")).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { expanded: false }));
  await userEvent.click(screen.getByRole("option", { name: /Jordan/ }));
  expect(screen.getByText("Audit console")).toBeInTheDocument();
  expect(screen.getByText("Admin")).toBeInTheDocument();
});

it("leaves Compare people out unless the demo flag is on", async () => {
  renderSidebar();
  expect(await screen.findByRole("link", { name: /My work/ })).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: /Compare people/ })).not.toBeInTheDocument();
});

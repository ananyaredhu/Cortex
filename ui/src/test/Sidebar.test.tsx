import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
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
  const link = await screen.findByRole("link", { name: "What's blocking cutover?" });
  expect(link).toHaveAttribute("href", "/ask/c_7");
});

it("shows console links only to the roles that may use them", async () => {
  renderSidebar();
  expect(screen.queryByText("Audit console")).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { expanded: false }));
  await userEvent.click(screen.getByRole("option", { name: /Jordan/ }));
  expect(screen.getByText("Audit console")).toBeInTheDocument();
  expect(screen.getByText("Admin")).toBeInTheDocument();
});

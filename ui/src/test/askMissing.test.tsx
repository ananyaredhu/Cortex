import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import { AskPage } from "../ask/AskPage";
import { PersonaProvider, usePersona } from "../auth/PersonaContext";
import type { PersonaId } from "../auth/personas";

const Where = () => <p data-testid="where">{useLocation().pathname}</p>;

let switchTo: (id: PersonaId) => void = () => {};
const Switcher = () => {
  switchTo = usePersona().setPersona;
  return null;
};

const empty = () => new Response(JSON.stringify({ conversations: [], suggested_questions: [] }));

function renderAsk(entry: string) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <PersonaProvider>
        <Switcher />
        <MemoryRouter initialEntries={[entry]}>
          <Where />
          <Routes>
            <Route path="/ask" element={<AskPage />} />
            <Route path="/ask/:conversationId" element={<AskPage />} />
          </Routes>
        </MemoryRouter>
      </PersonaProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => localStorage.clear());

it("lands an opened link to a conversation that is not the caller's, or is gone, on a new question with one notice", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(async (url: string) =>
      String(url).includes("/v1/conversations/c_gone")
        ? new Response(JSON.stringify({ detail: "Not found" }), { status: 404 })
        : empty(),
    ),
  );
  renderAsk("/ask/c_gone");
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/^\/ask$/));
  expect(await screen.findByRole("alert")).toHaveTextContent("That conversation isn’t available.");
});

it("starts a clean new question, with no notice and no request for the old one, when the persona changes", async () => {
  const fetch = vi.fn().mockImplementation(async (url: string) =>
    String(url).includes("/v1/conversations/c_priya")
      ? new Response(JSON.stringify({ conversation_id: "c_priya", turns: [] }))
      : empty(),
  );
  vi.stubGlobal("fetch", fetch);
  renderAsk("/ask/c_priya");
  await waitFor(() => expect(screen.queryByText("Opening the conversation…")).not.toBeInTheDocument());
  const before = fetch.mock.calls.filter(([u]) => String(u).includes("c_priya")).length;

  act(() => switchTo("dana"));

  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/^\/ask$/));
  expect(screen.getByText("New question")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.queryByText(/isn’t available/)).not.toBeInTheDocument();
  // Dana's browser never asks for Priya's conversation, so the Brain has nothing to refuse.
  expect(fetch.mock.calls.filter(([u]) => String(u).includes("c_priya")).length).toBe(before);
});

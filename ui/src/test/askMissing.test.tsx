import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import { AskPage } from "../ask/AskPage";
import { PersonaProvider } from "../auth/PersonaContext";

const Where = () => <p data-testid="where">{useLocation().pathname}</p>;

beforeEach(() => localStorage.clear());

it("opens a plain new question, with no notice, when the conversation in the address is not the caller's or is gone", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(async (url: string) =>
      String(url).includes("/v1/conversations/c_gone")
        ? new Response(JSON.stringify({ detail: "Not found" }), { status: 404 })
        : new Response(JSON.stringify({ conversations: [], suggested_questions: [] })),
    ),
  );
  render(
    <QueryClientProvider client={new QueryClient()}>
      <PersonaProvider>
        <MemoryRouter initialEntries={["/ask/c_gone"]}>
          <Where />
          <Routes>
            <Route path="/ask" element={<AskPage />} />
            <Route path="/ask/:conversationId" element={<AskPage />} />
          </Routes>
        </MemoryRouter>
      </PersonaProvider>
    </QueryClientProvider>,
  );
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/^\/ask$/));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.queryByText(/isn’t available/)).not.toBeInTheDocument();
});

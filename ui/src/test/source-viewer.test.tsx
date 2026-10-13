import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import { PersonaProvider } from "../auth/PersonaContext";
import { isPublicUrl, SourceLink, SourceViewerProvider } from "../source/SourceViewer";

const DOC = {
  found: true,
  doc_id: "jira:DBMIG-142",
  title: "DBMIG-142 Cutover blocked on replica lag",
  url: "http://localhost:8102/browse/DBMIG-142",
  source: "jira",
  as_of: "2026-10-10T08:00:00Z",
  text: "Database migration cutover is blocked: replica lag stays above the 5 second threshold.",
  truncated: false,
  why_visible: ["user:priya@companya.com", "role:DBMIG:developer"],
  flags: [],
};

const setup = (answer: unknown) => {
  const fetchMock = vi.fn().mockImplementation(async () => new Response(JSON.stringify(answer)));
  vi.stubGlobal("fetch", fetchMock);
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <PersonaProvider>
        <MemoryRouter>
          <SourceViewerProvider>
            <SourceLink docId="jira:DBMIG-142" title="Cutover blocked" href="http://localhost:8102/browse/DBMIG-142">
              Cutover blocked
            </SourceLink>
          </SourceViewerProvider>
        </MemoryRouter>
      </PersonaProvider>
    </QueryClientProvider>,
  );
  return fetchMock;
};

beforeEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

it("opens the document inside the app instead of going to the source address", async () => {
  const fetchMock = setup(DOC);
  await userEvent.click(screen.getByRole("link", { name: "Cutover blocked" }));
  const panel = await screen.findByRole("dialog");
  expect(await screen.findByText(/replica lag stays above/)).toBeInTheDocument();
  expect(panel).toHaveTextContent("DBMIG-142 Cutover blocked on replica lag");
  expect(panel).toHaveTextContent("role:DBMIG:developer");
  expect(String(fetchMock.mock.calls[0][0])).toContain("/v1/source?doc_id=jira%3ADBMIG-142");
  // the address is on localhost, which a reader outside the company cannot open: no "Open the original" link
  expect(screen.queryByText(/Open the original/)).not.toBeInTheDocument();
});

it("shows one message for a document that is forbidden or does not exist", async () => {
  setup({ found: false });
  await userEvent.click(screen.getByRole("link", { name: "Cutover blocked" }));
  expect(await screen.findByRole("status")).toHaveTextContent("Cortex can't show this source to you.");
  expect(screen.queryByText(/replica lag/)).not.toBeInTheDocument();
});

it("closes on Escape and gives focus back to the link", async () => {
  setup(DOC);
  const link = screen.getByRole("link", { name: "Cutover blocked" });
  await userEvent.click(link);
  await screen.findByRole("dialog");
  await userEvent.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(link).toHaveFocus();
});

it("asks as the person whose column it was opened from, not as whoever is signed in", async () => {
  const fetchMock = vi.fn().mockImplementation(async () => new Response(JSON.stringify({ found: false })));
  vi.stubGlobal("fetch", fetchMock);
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <PersonaProvider>
        <MemoryRouter>
          <SourceViewerProvider>
            <SourceLink docId="jira:DBMIG-142" asker="sam" href="https://x/DBMIG-142">
              Open
            </SourceLink>
          </SourceViewerProvider>
        </MemoryRouter>
      </PersonaProvider>
    </QueryClientProvider>,
  );
  await userEvent.click(screen.getByRole("link", { name: "Open" }));
  await screen.findByRole("status");
  const headers = new Headers(fetchMock.mock.calls[0][1]?.headers);
  expect(headers.get("authorization")).toBe("Bearer dev:sam");
});

it("keeps the address on the link when there is no viewer around it", async () => {
  vi.stubGlobal("fetch", vi.fn());
  render(
    <SourceLink docId="jira:DBMIG-142" href="https://x/DBMIG-142">
      Open
    </SourceLink>,
  );
  expect(screen.getByRole("link", { name: "Open" })).toHaveAttribute("href", "https://x/DBMIG-142");
});

it("offers the original only for an address a reader outside could open", () => {
  expect(isPublicUrl("https://acme.atlassian.net/browse/X-1")).toBe(true);
  for (const u of ["http://localhost:8102/browse/X", "http://127.0.0.1/x", "http://10.1.2.3/x", "http://172.20.0.5/x", "http://192.168.1.2/x", "javascript:alert(1)", "not a url"]) {
    expect(isPublicUrl(u)).toBe(false);
  }
});

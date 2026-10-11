import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import type { AuditAnswer, AuditEvent } from "../api/types";
import { AuditPage } from "../audit/AuditPage";
import { PersonaProvider } from "../auth/PersonaContext";

// The audit console's two ways in: "Ask" (POST /v1/audit/ask, the audit agent) and "Run query" (POST /v1/audit/query).

const EVENT = {
  seq: 39,
  ts: "2026-10-09T06:06:00Z",
  event_type: "ask",
  request_id: "req_1",
  actor: { user_id: "priya@companya.com", roles: ["engineer"], client: "ui" },
  query: { text: "What's the latest runbook for payment-service incident failover?", skill: null },
  decisions: [{ doc_id: "confluence:PAY/runbook-payment-service", allowed: true }],
  flags: [],
} as unknown as AuditEvent;

const VERIFY = { ok: true, checked: 312, checkpoints: 3 };

const serve = (answer: AuditAnswer) => {
  const calls: { path: string; body: unknown }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      calls.push({ path, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      if (path.endsWith("/v1/audit/ask")) return new Response(JSON.stringify(answer));
      if (path.endsWith("/v1/audit/query")) return new Response(JSON.stringify({ events: [EVENT], count: 1 }));
      return new Response(JSON.stringify(VERIFY));
    }),
  );
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <PersonaProvider>
        <AuditPage />
      </PersonaProvider>
    </QueryClientProvider>,
  );
  return calls;
};

const type = (text: string) => fireEvent.change(screen.getByLabelText("Audit question"), { target: { value: text } });

beforeEach(() => localStorage.clear());

it("Ask sends the question in words and shows how it was understood, the summary and the table", async () => {
  const calls = serve({
    question: "What did Priya access in the PAY space in the last 30 days?",
    plan: { kind: "query", filter: { user: "priya@companya.com", space: "confluence:PAY" }, understood: "Events of priya@companya.com touching PAY.", source: "rules" },
    clarify: null,
    summary: "1 event (1 ask). Access decisions: 1 allowed, 0 denied.",
    result: { events: [EVENT], count: 1 },
  });
  type("What did Priya access in the PAY space in the last 30 days?");
  fireEvent.click(screen.getByRole("button", { name: "Ask" }));

  expect(await screen.findByText("Events of priya@companya.com touching PAY.")).toBeInTheDocument();
  expect(screen.getByText("Understood as:")).toBeInTheDocument();
  expect(screen.getByText("1 event (1 ask). Access decisions: 1 allowed, 0 denied.")).toBeInTheDocument();
  expect(within(screen.getByRole("table")).getByText(/latest runbook/)).toBeInTheDocument();
  const sent = calls.find((c) => c.path.endsWith("/v1/audit/ask"));
  expect(sent?.body).toEqual({ question: "What did Priya access in the PAY space in the last 30 days?" });
  expect(calls.some((c) => c.path.endsWith("/v1/audit/query"))).toBe(false);
});

it("a time-travel answer lists what the person could open and what they lost, with restricted titles withheld", async () => {
  serve({
    question: "What could Priya see on 11 October 2026?",
    plan: { kind: "time_travel", user: "priya@companya.com", at: "2026-10-11T00:00:00Z", understood: "What priya@companya.com could open then, and what changed since.", source: "rules" },
    clarify: null,
    summary: "2 documents then; 1 lost since.",
    result: {
      user: "priya@companya.com",
      at: "2026-10-11T00:00:00Z",
      identity: { known: true },
      could_see: [
        { doc_id: "jira:DBMIG-142", title: "DBMIG-142 Cutover blocked on replica lag", source: "jira" },
        { doc_id: "slack:C_AUTHPRIV/thread-1", restricted: true },
      ],
      changed_since: { lost: [{ doc_id: "slack:C_AUTHPRIV/thread-1", restricted: true }], gained: [] },
    },
  });
  type("What could Priya see on 11 October 2026?");
  fireEvent.click(screen.getByRole("button", { name: "Ask" }));

  const could = await screen.findByRole("region", { name: /Could open at/ });
  expect(within(could).getByText("DBMIG-142 Cutover blocked on replica lag")).toBeInTheDocument();
  expect(within(could).getByText(/title is withheld/)).toBeInTheDocument();
  expect(within(screen.getByRole("region", { name: "Lost since" })).getByText("slack:C_AUTHPRIV/thread-1")).toBeInTheDocument();
  expect(within(screen.getByRole("region", { name: "Gained since" })).getByText("Nothing gained since then.")).toBeInTheDocument();
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
});

it("a question that could not be planned shows the clarifying message and no table", async () => {
  serve({ question: "anything odd?", plan: null, clarify: "Name a person, a space or a date.", result: undefined });
  type("anything odd?");
  fireEvent.click(screen.getByRole("button", { name: "Ask" }));
  expect(await screen.findByText("Name a person, a space or a date.")).toBeInTheDocument();
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
  expect(screen.queryByText("Understood as:")).not.toBeInTheDocument();
});

it("Run query still sends the filters as they are", async () => {
  const calls = serve({ question: "", plan: null, clarify: null });
  fireEvent.click(screen.getByRole("button", { name: "Run query" }));
  expect(await screen.findByRole("table")).toBeInTheDocument();
  const sent = calls.find((c) => c.path.endsWith("/v1/audit/query"));
  expect((sent?.body as { filter: unknown }).filter).toEqual({ user: "priya@companya.com", space: "confluence:PAY", decision: "all" });
  expect(calls.some((c) => c.path.endsWith("/v1/audit/ask"))).toBe(false);
  expect(screen.queryByText("Understood as:")).not.toBeInTheDocument();
});

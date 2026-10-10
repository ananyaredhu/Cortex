import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { AnswerCard } from "../ask/AnswerCard";
import { answerSegments, banners } from "../ask/format";
import { TrustPanel } from "../ask/TrustPanel";
import { PersonaProvider } from "../auth/PersonaContext";
import { ANSWER, refusal, unavailable } from "./fixtures";

const wrap = (ui: ReactNode) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <PersonaProvider>{ui}</PersonaProvider>
    </QueryClientProvider>,
  );

describe("answerSegments", () => {
  it("puts each citation marker after the claim it supports, numbered by citations order", () => {
    const segs = answerSegments(ANSWER);
    expect(segs.map((s) => (s.kind === "text" ? s.text : `[${s.n}]`)).join("")).toBe(
      "Cutover is blocked on replica lag.[1] Blockers were raised in #db-migration last week.[2][1]",
    );
  });

  it("falls back to the plain answer when there are no claims", () => {
    expect(answerSegments(refusal("req_1"))).toEqual([{ kind: "text", text: refusal("req_1").answer }]);
  });
});

describe("banners", () => {
  it("names unreachable and stale sources", () => {
    const r = {
      ...ANSWER,
      unavailable_sources: ["slack" as const],
      freshness: {
        ...ANSWER.freshness!,
        per_source: { ...ANSWER.freshness!.per_source, confluence: { last_sync: "x", status: "stale" as const } },
      },
    };
    expect(banners(r).map((b) => b.text)).toEqual([
      "Slack couldn't be reached, so this answer comes from the other sources only.",
      "Confluence is behind its sync target, so the answer may miss recent changes.",
    ]);
  });

  it("is empty when every source is fine", () => {
    expect(banners(ANSWER)).toEqual([]);
  });
});

describe("AnswerCard", () => {
  it("renders a forbidden and a nonexistent refusal identically", () => {
    // Scenario 3: the stub returns the same body apart from the ids for Sam's breach question
    // and for a report that doesn't exist. The card must not add any difference of its own.
    const a = wrap(<AnswerCard asker="sam" answer={refusal("req_0007")} onWhyMore={() => {}} onChoose={() => {}} />);
    const htmlA = a.container.innerHTML;
    a.unmount();
    const b = wrap(<AnswerCard asker="sam" answer={refusal("req_0008")} onWhyMore={() => {}} onChoose={() => {}} />);
    expect(b.container.innerHTML).toBe(htmlA);
    expect(screen.getByText("No sources to show for this answer.")).toBeInTheDocument();
  });

  it("shows the source excerpt when a citation marker gets focus", () => {
    wrap(<AnswerCard asker="sam" answer={ANSWER} onWhyMore={() => {}} onChoose={() => {}} />);
    fireEvent.focus(screen.getAllByRole("button", { name: /Source 1:/ })[0]);
    expect(screen.getByRole("tooltip")).toHaveTextContent("replica lag stays above the 5 second threshold");
  });

  it("asks the chosen clarification", () => {
    const onChoose = vi.fn();
    const r = { ...ANSWER, clarify: { question: "Which migration?", options: ["DBMIG", "Ledger"] } };
    wrap(<AnswerCard asker="sam" answer={r} onWhyMore={() => {}} onChoose={onChoose} />);
    fireEvent.click(screen.getByRole("button", { name: "Ledger" }));
    expect(onChoose).toHaveBeenCalledWith("Ledger");
  });
});

describe("answer service unavailable", () => {
  const noop = () => {};

  it("is told apart from an abstention, a refusal and a normal answer", () => {
    expect(banners(unavailable("req_1")).map((b) => b.kind)).toEqual(["unavailable"]);
    expect(banners({ ...unavailable("req_1"), generator_unavailable: false, abstained: true })).toEqual([]);
    expect(banners(refusal("req_2"))).toEqual([]);
    expect(banners(ANSWER)).toEqual([]);
  });

  it("comes before the unreachable-sources banner", () => {
    const r = { ...unavailable("req_3"), unavailable_sources: ["slack" as const] };
    expect(banners(r).map((b) => b.kind)).toEqual(["unavailable", "error"]);
  });

  it("shows an accessible status banner, and not the abstention wording or the refusal hint", () => {
    wrap(<AnswerCard asker="priya" answer={unavailable("req_5")} onWhyMore={noop} onChoose={noop} />);
    expect(screen.getByRole("status")).toHaveTextContent("The answer service is unavailable right now");
    expect(screen.getByText(/temporarily unavailable/)).toBeInTheDocument(); // the Brain's own message stays as the answer
    expect(screen.queryByText(/none of them supports/)).toBeNull();
    expect(screen.queryByText(/If you think it should exist/)).toBeNull();
  });

  it("adds nothing to a refusal: a forbidden and a nonexistent document still look identical", () => {
    const a = wrap(<AnswerCard asker="sam" answer={refusal("req_7")} onWhyMore={noop} onChoose={noop} />);
    expect(screen.queryByRole("status")).toBeNull();
    const html = a.container.innerHTML;
    a.unmount();
    const b = wrap(<AnswerCard asker="sam" answer={{ ...refusal("req_8"), generator_unavailable: false }} onWhyMore={noop} onChoose={noop} />);
    expect(b.container.innerHTML).toBe(html.replace("req_7", "req_8"));
  });
});

describe("TrustPanel", () => {
  it("shows cited counts per source and nothing about what was withheld", () => {
    const { container } = wrap(<TrustPanel answer={ANSWER} highlightWhy={false} onClose={() => {}} />);
    const text = container.textContent!.toLowerCase();
    expect(text).not.toMatch(/found|denied|allowed|withheld/);
    expect(screen.getByText("100%")).toBeInTheDocument();
    expect(screen.getByText("req_0001")).toBeInTheDocument();
  });
});

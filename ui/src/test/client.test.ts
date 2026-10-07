import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";

afterEach(() => vi.unstubAllGlobals());

function sse(blocks: string[]): Response {
  const body = new ReadableStream({
    start(c) {
      for (const b of blocks) c.enqueue(new TextEncoder().encode(b));
      c.close();
    },
  });
  return new Response(body, { status: 200 });
}

describe("api client", () => {
  it("sends the persona's token on every request", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ conversations: [] })));
    vi.stubGlobal("fetch", fetchMock);
    await api("sam").conversations();
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe("Bearer dev:sam");
  });

  it("reads stages and the result from the stream, even when split across chunks", async () => {
    const result = { request_id: "req_1", refused: true, citations: [] };
    const text = `event: stage\ndata: {"stage": "retrieve", "status": "start"}\n\nevent: result\ndata: ${JSON.stringify(result)}\n\n`;
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sse([text.slice(0, 30), text.slice(30)])));
    const seen: string[] = [];
    const r = await api("priya").askStream({ question: "q" }, (s, st) => seen.push(`${s}:${st}`));
    expect(seen).toEqual(["retrieve:start"]);
    expect(r.request_id).toBe("req_1");
  });
});

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { forgetToken } from "../auth/token";
import { ApiError, api } from "../api/client";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

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

  it("does not retry a 401 in dev mode: it is a real refusal", async () => {
    const fetchMock = vi.fn().mockImplementation(async () => new Response("{}", { status: 401 }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(api("sam").conversations()).rejects.toBeInstanceOf(ApiError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("api client with the mock IdP", () => {
  let issued: number;

  /** Routes /idp/token to a mock IdP and everything else to `handle`, which sees the Authorization header. */
  function server(handle: (authorization: string, apiCall: number) => Response) {
    let apiCalls = 0;
    const seen: string[] = [];
    const fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      if (url === "/idp/token") {
        issued += 1;
        return new Response(JSON.stringify({ access_token: `tok${issued}`, token_type: "Bearer", expires_in: 900 }));
      }
      apiCalls += 1;
      const authorization = (init?.headers as Record<string, string>).Authorization;
      seen.push(authorization);
      return handle(authorization, apiCalls);
    });
    vi.stubGlobal("fetch", fetchMock);
    return { fetchMock, seen };
  }

  beforeEach(() => {
    issued = 0;
    forgetToken();
    vi.stubEnv("VITE_AUTH_MODE", "idp");
  });

  it("signs in first and sends the fetched token", async () => {
    const { seen } = server(() => new Response(JSON.stringify({ conversations: [] })));
    await api("sam").conversations();
    await api("sam").conversations();
    expect(seen).toEqual(["Bearer tok1", "Bearer tok1"]); // one sign-in, reused
    expect(issued).toBe(1);
  });

  it("drops a refused token, signs in again and retries exactly once", async () => {
    const { seen } = server((authorization) =>
      authorization === "Bearer tok1"
        ? new Response("{}", { status: 401 })
        : new Response(JSON.stringify({ conversations: [{ conversation_id: "c_1" }] })),
    );
    const out = await api("priya").conversations();
    expect(out.conversations).toHaveLength(1);
    expect(seen).toEqual(["Bearer tok1", "Bearer tok2"]);
    expect(issued).toBe(2);
  });

  it("gives up after one retry instead of looping", async () => {
    const { seen } = server(() => new Response("{}", { status: 401 }));
    await expect(api("priya").conversations()).rejects.toMatchObject({ status: 401 });
    expect(seen).toHaveLength(2);
  });

  it("uses the token on the streaming request too", async () => {
    const result = { request_id: "req_9", refused: false, citations: [] };
    const { seen } = server(() => sse([`event: result\ndata: ${JSON.stringify(result)}\n\n`]));
    const r = await api("dana").askStream({ question: "q" }, () => {});
    expect(r.request_id).toBe("req_9");
    expect(seen).toEqual(["Bearer tok1"]);
  });
});

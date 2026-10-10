import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SignInError, authHeader, authMode, forgetToken } from "../auth/token";

let counter = 0;

/** A mock IdP: every call returns a new token, so a test can tell a cached token from a fresh one. */
function idp(expiresIn = 900) {
  return vi.fn().mockImplementation(async () => {
    counter += 1;
    return new Response(JSON.stringify({ access_token: `tok${counter}`, token_type: "Bearer", expires_in: expiresIn }));
  });
}

beforeEach(() => {
  counter = 0;
  forgetToken();
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("dev mode (the default)", () => {
  it("sends the dev token and never calls the IdP", async () => {
    const fetchMock = idp();
    vi.stubGlobal("fetch", fetchMock);
    expect(authMode()).toBe("dev");
    expect(await authHeader("sam")).toBe("Bearer dev:sam");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("idp mode", () => {
  beforeEach(() => vi.stubEnv("VITE_AUTH_MODE", "idp"));

  it("is selected by VITE_AUTH_MODE or by the idp Vite mode", () => {
    expect(authMode()).toBe("idp");
    vi.unstubAllEnvs();
    vi.stubEnv("MODE", "idp");
    expect(authMode()).toBe("idp");
  });

  it("signs in once and reuses the token", async () => {
    const fetchMock = idp();
    vi.stubGlobal("fetch", fetchMock);
    expect(await authHeader("priya")).toBe("Bearer tok1");
    expect(await authHeader("priya")).toBe("Bearer tok1");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/idp/token");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ persona: "priya" });
  });

  it("keeps a separate token for each persona", async () => {
    const fetchMock = idp();
    vi.stubGlobal("fetch", fetchMock);
    expect(await authHeader("priya")).toBe("Bearer tok1");
    expect(await authHeader("sam")).toBe("Bearer tok2");
    expect(await authHeader("priya")).toBe("Bearer tok1");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("lets simultaneous requests share one sign-in", async () => {
    const fetchMock = idp();
    vi.stubGlobal("fetch", fetchMock);
    const headers = await Promise.all([authHeader("dana"), authHeader("dana"), authHeader("dana")]);
    expect(headers).toEqual(["Bearer tok1", "Bearer tok1", "Bearer tok1"]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("renews the token shortly before it expires", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-10T12:00:00Z"));
    const fetchMock = idp(100); // lives 100 s; renewed once less than 30 s is left
    vi.stubGlobal("fetch", fetchMock);
    expect(await authHeader("maya")).toBe("Bearer tok1");
    vi.setSystemTime(new Date("2026-10-10T12:01:00Z")); // 40 s left: still good
    expect(await authHeader("maya")).toBe("Bearer tok1");
    vi.setSystemTime(new Date("2026-10-10T12:01:20Z")); // 20 s left: renew
    expect(await authHeader("maya")).toBe("Bearer tok2");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("forgetToken makes the next call sign in again", async () => {
    const fetchMock = idp();
    vi.stubGlobal("fetch", fetchMock);
    await authHeader("jordan");
    forgetToken("jordan");
    expect(await authHeader("jordan")).toBe("Bearer tok2");
  });

  it("explains a missing mock IdP and recovers once it exists", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 404 })));
    const failure = await authHeader("priya").catch((e) => e);
    expect(failure).toBeInstanceOf(SignInError);
    expect(failure.status).toBe(404);
    expect(failure.message).toContain("BRAIN_MOCK_IDP=1");

    vi.stubGlobal("fetch", idp()); // the failed attempt must not be remembered
    expect(await authHeader("priya")).toBe("Bearer tok1");
  });

  it("passes on other failures with the server's status", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 500, statusText: "Server Error" })));
    const failure = await authHeader("sam").catch((e) => e);
    expect(failure).toBeInstanceOf(SignInError);
    expect(failure.status).toBe(500);
  });
});

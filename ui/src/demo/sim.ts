/**
 * Demo-only helpers. They call the stub API's /sim endpoints; against the real system the same controls
 * should call Workstream A's simulator admin endpoints (simulators/README.md). Only built in when
 * DEMO_CONTROLS is true (`npm run dev:demo`), never in a production build.
 */
export const DEMO_CONTROLS = import.meta.env.MODE === "demo" || import.meta.env.VITE_DEMO_CONTROLS === "1";

export async function sim(path: string, body?: unknown): Promise<unknown> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

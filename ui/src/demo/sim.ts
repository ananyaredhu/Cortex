/**
 * Demo-only helpers. They call the stub API's /sim endpoints; against the real system the same controls
 * should call Workstream A's simulator admin endpoints (simulators/README.md). Only built in when
 * DEMO_CONTROLS is true (`npm run dev:demo`), never in a production build.
 */
export const DEMO_CONTROLS =
  import.meta.env.MODE === "demo" || import.meta.env.MODE === "idp" || import.meta.env.VITE_DEMO_CONTROLS === "1";

/**
 * Compare people (/compare) asks one question as several personas side by side. It exists to show the access rules in
 * a demo and is not a product screen: off unless the UI runs in demo mode (`npm run dev:demo`) or VITE_COMPARE_PEOPLE=1.
 */
export const COMPARE_PEOPLE = import.meta.env.MODE === "demo" || import.meta.env.VITE_COMPARE_PEOPLE === "1";

export async function sim(path: string, body?: unknown): Promise<unknown> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

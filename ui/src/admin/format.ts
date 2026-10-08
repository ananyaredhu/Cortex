import type { FreshnessReport } from "../api/types";

/** 45 → "45 s", 300 → "5 min", 5400 → "1.5 h" */
export function duration(seconds: number): string {
  if (seconds < 90) return `${Math.round(seconds)} s`;
  if (seconds < 90 * 60) return `${Math.round(seconds / 60)} min`;
  return `${(seconds / 3600).toFixed(1).replace(/\.0$/, "")} h`;
}

export type Health = { tone: "ok" | "warn" | "bad" | "none"; label: string };

/** Connector status against the SLA: under 5 minutes for events, never over 1 hour (docs/04-scenarios.md). */
export function health(s: FreshnessReport["sources"][string]): Health {
  if (s.last_error) return { tone: "bad", label: "Failing" };
  if (!s.freshness_lag_seconds.count) return { tone: "none", label: "No changes yet" };
  if (s.freshness_lag_seconds.p95 > 3600) return { tone: "bad", label: "Beyond 1 h" };
  if (s.freshness_lag_seconds.p95 > 300) return { tone: "warn", label: "Lagging" };
  return { tone: "ok", label: "Within SLA" };
}

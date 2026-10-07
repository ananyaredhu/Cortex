import { describe, expect, it } from "vitest";
import { duration, health } from "../admin/format";
import type { AuditEvent } from "../api/types";
import { counts, shortHash, sourcesHit } from "../audit/format";

const lag = (p95: number, count = 3) => ({ count, p50: p95 / 2, p95, max: p95 });
const source = (p95: number, extra = {}) => ({
  last_run_at: null,
  last_ok_at: null,
  last_error: null,
  freshness_lag_seconds: lag(p95),
  pipeline_lag_seconds: lag(p95),
  ...extra,
});

describe("admin format", () => {
  it("formats durations at a readable unit", () => {
    expect([duration(45), duration(300), duration(5400)]).toEqual(["45 s", "5 min", "1.5 h"]);
  });

  it("rates connectors against the 5-minute and 1-hour SLA", () => {
    expect(health(source(120)).label).toBe("Within SLA");
    expect(health(source(900)).tone).toBe("warn");
    expect(health(source(7200)).tone).toBe("bad");
    expect(health(source(60, { last_error: "ConnectTimeout" })).label).toBe("Failing");
  });
});

describe("audit format", () => {
  const event = {
    decisions: [
      { doc_id: "confluence:PAY/runbook", allowed: true, acl_snapshot_hash: "", policy_version: "" },
      { doc_id: "slack:C_PAYINC/thread-1", allowed: true, acl_snapshot_hash: "", policy_version: "" },
      { doc_id_hash: "sha256:abc", allowed: false, acl_snapshot_hash: "", policy_version: "" },
    ],
  } as unknown as AuditEvent;

  it("counts decisions and lists sources from allowed ones only", () => {
    expect(counts(event)).toEqual({ allowed: 2, denied: 1 });
    expect(sourcesHit(event)).toEqual(["confluence", "slack"]);
  });

  it("shortens hashes", () => {
    expect(shortHash("sha256:9e4b71d0aaaa3fa8c21a")).toBe("9e4b…c21a");
  });
});

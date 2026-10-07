import { useState } from "react";
import type { FreshnessReport, Source } from "../api/types";
import { SOURCE_LABEL } from "../components/SourceGlyph";
import { duration } from "./format";

const TARGET_S = 300; // 5 minutes for event-driven changes (docs/04-scenarios.md)
const WORST_S = 3600; // never more than 1 hour

/**
 * p95 freshness lag (source edit → searchable) per source against the 5-minute target. One measure, one
 * neutral bar colour; the source name labels each bar and the numbers sit beside it, so nothing relies on colour.
 */
export function LagChart({ report }: { report: FreshnessReport }) {
  const [hover, setHover] = useState<string | null>(null);
  const rows = Object.entries(report.sources).map(([name, s]) => ({ name, ...s.freshness_lag_seconds }));
  const peak = Math.max(...rows.map((r) => r.p95), 0);
  const domain = Math.max(TARGET_S * 1.2, peak * 1.15);
  const ticks = niceTicks(domain);
  const x = (v: number) => `${(v / domain) * 100}%`;

  return (
    <figure className="lag" aria-label="p95 freshness lag per source">
      <div className="lag__plot">
        <div className="lag__axis" aria-hidden>
          {ticks.map((t) => (
            <div key={t} className="lag__grid" style={{ left: x(t) }}>
              <span>{duration(t)}</span>
            </div>
          ))}
          <div className="lag__target" style={{ left: x(TARGET_S) }}>
            <span>5 min target</span>
          </div>
          {domain > WORST_S && (
            <div className="lag__target lag__target--worst" style={{ left: x(WORST_S) }}>
              <span>1 h limit</span>
            </div>
          )}
        </div>
        {rows.map((r) => (
          <div
            key={r.name}
            className="lag__row"
            onMouseEnter={() => setHover(r.name)}
            onMouseLeave={() => setHover(null)}
            onFocus={() => setHover(r.name)}
            onBlur={() => setHover(null)}
            tabIndex={0}
            aria-label={`${label(r.name)}: p95 ${duration(r.p95)}, p50 ${duration(r.p50)}, ${r.count} changes`}
          >
            <span className="lag__name">{label(r.name)}</span>
            <span className="lag__track">
              <span className={`lag__bar${hover === r.name ? " is-hover" : ""}`} style={{ width: r.count ? x(r.p95) : 0 }} />
            </span>
            <span className="lag__value mono">{r.count ? duration(r.p95) : "none yet"}</span>
            {hover === r.name && (
              <span className="lag__tip" role="tooltip">
                <strong>{label(r.name)}</strong>
                <span>p50 {duration(r.p50)}</span>
                <span>p95 {duration(r.p95)}</span>
                <span>max {duration(r.max)}</span>
                <span>{r.count} changes</span>
              </span>
            )}
          </div>
        ))}
      </div>
      <figcaption className="muted small">
        p95 time from an edit in the source to it being searchable, last {report.window_hours} h.
      </figcaption>
    </figure>
  );
}

function label(name: string): string {
  return SOURCE_LABEL[name as Source] ?? name;
}

function niceTicks(domain: number): number[] {
  const steps = [30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 14400];
  const step = steps.find((s) => domain / s <= 5) ?? 28800;
  const out: number[] = [];
  for (let t = 0; t <= domain; t += step) out.push(t);
  return out;
}

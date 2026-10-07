import { useQueryClient } from "@tanstack/react-query";
import { FlaskConical, X } from "lucide-react";
import { useState } from "react";
import { SOURCES, type Source } from "../api/types";
import { SOURCE_LABEL } from "../components/SourceGlyph";
import "./demo.css";
import { sim } from "./sim";

/** Demo-only controls for the scripted changes in the scenarios (see ./sim.ts). */
const EVENTS = [
  { id: "e1", scenario: "Scenario 2", label: "Runbook owner adds a failover step" },
  { id: "e2", scenario: "Scenario 4", label: "Remove Priya from #auth-private" },
];

export function DemoControls() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [applied, setApplied] = useState<string[]>([]);
  const [status, setStatus] = useState<Partial<Record<Source, string>>>({});
  const [seq, setSeq] = useState("2");
  const [note, setNote] = useState<string | null>(null);

  const run = async (label: string, f: () => Promise<unknown>) => {
    try {
      await f();
      setNote(`Done: ${label}`);
      qc.invalidateQueries();
    } catch (e) {
      setNote(`Failed: ${(e as Error).message}`);
    }
  };

  if (!open) {
    return (
      <button type="button" className="demo__fab" onClick={() => setOpen(true)} aria-label="Open demo controls">
        <FlaskConical size={16} aria-hidden /> Demo
      </button>
    );
  }

  return (
    <aside className="demo" aria-label="Demo controls">
      <header className="demo__head">
        <strong>Demo controls</strong>
        <span className="muted small">stub only</span>
        <button type="button" className="icon-button" onClick={() => setOpen(false)} aria-label="Close demo controls">
          <X size={16} />
        </button>
      </header>

      <section>
        <h2 className="eyebrow">Scripted changes</h2>
        {EVENTS.map((e) => (
          <button
            key={e.id}
            type="button"
            className="demo__action"
            disabled={applied.includes(e.id)}
            onClick={() =>
              run(e.label, async () => {
                const r = (await sim("/sim/advance", { event_id: e.id })) as { applied: string[] };
                setApplied(r.applied);
              })
            }
          >
            <span className="muted small">
              {e.scenario} · {e.id}
            </span>
            <span>{applied.includes(e.id) ? `Applied: ${e.label}` : e.label}</span>
          </button>
        ))}
      </section>

      <section>
        <h2 className="eyebrow">Source health</h2>
        {SOURCES.map((s) => (
          <label key={s} className="demo__row">
            <span>{SOURCE_LABEL[s]}</span>
            <select
              value={status[s] ?? "ok"}
              onChange={(e) => {
                const value = e.target.value;
                run(`${SOURCE_LABEL[s]} ${value}`, async () => {
                  await sim("/sim/source-status", { source: s, status: value });
                  setStatus((cur) => ({ ...cur, [s]: value }));
                });
              }}
            >
              <option value="ok">Up to date</option>
              <option value="stale">Behind</option>
              <option value="unavailable">Unreachable</option>
            </select>
          </label>
        ))}
      </section>

      <section>
        <h2 className="eyebrow">Audit log</h2>
        <div className="demo__row">
          <label htmlFor="tamper-seq">Tamper with entry</label>
          <input id="tamper-seq" inputMode="numeric" value={seq} onChange={(e) => setSeq(e.target.value)} />
          <button
            type="button"
            className="ghost"
            onClick={() => run(`tampered with entry ${seq}`, () => sim(`/sim/tamper?seq=${encodeURIComponent(seq)}`))}
          >
            Tamper
          </button>
        </div>
      </section>

      <section>
        <button
          type="button"
          className="demo__reset"
          onClick={() =>
            run("reset", async () => {
              await sim("/sim/reset");
              setApplied([]);
              setStatus({});
            })
          }
        >
          Reset everything
        </button>
        {note && (
          <p className="small muted" role="status">
            {note}
          </p>
        )}
      </section>
    </aside>
  );
}

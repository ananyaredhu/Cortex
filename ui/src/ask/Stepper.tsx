import { Check, Circle, LoaderCircle } from "lucide-react";
import { STAGES, type Stage } from "../api/types";

export const STAGE_LABEL: Record<Stage, string> = {
  retrieve: "Finding sources",
  authorize: "Checking your access",
  verify_live: "Verifying with source systems",
  generate: "Writing answer",
  check: "Checking citations",
};

export type StageTimes = Partial<Record<Stage, { start: number; end?: number }>>;

/** Real pipeline progress from `/v1/ask/stream`: each step moves only when the server says so. */
export function Stepper({ times }: { times: StageTimes }) {
  return (
    <ol className="stepper" aria-live="polite" aria-label="Progress">
      {STAGES.map((s) => {
        const t = times[s];
        const state = !t ? "waiting" : t.end === undefined ? "running" : "done";
        return (
          <li key={s} data-state={state}>
            {state === "done" ? (
              <Check size={14} aria-hidden />
            ) : state === "running" ? (
              <LoaderCircle size={14} className="spin" aria-hidden />
            ) : (
              <Circle size={14} aria-hidden />
            )}
            <span>{STAGE_LABEL[s]}</span>
            <span className="mono muted">{t?.end !== undefined ? `${Math.round(t.end - t.start)} ms` : ""}</span>
          </li>
        );
      })}
    </ol>
  );
}

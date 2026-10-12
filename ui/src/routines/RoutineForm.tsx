import { useMutation, useQueryClient } from "@tanstack/react-query";
import { X } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { api, ApiError } from "../api/client";
import { type Routine, type RoutineSchedule, SOURCES, type Source } from "../api/types";
import type { PersonaId } from "../auth/personas";
import { SOURCE_LABEL } from "../components/SourceGlyph";
import { browserZone, WEEKDAYS } from "./format";
import "./routines.css";

export interface RoutineSeed {
  question?: string;
  sources?: Source[];
}

/**
 * The one form for a routine: new (from the Routines list, the Ask composer or an answer) or an existing one being
 * edited. The schedule is fixed fields, never free text: nothing parses "every other Tuesday", and no model sees it
 * (docs/routines.md, F3).
 */
export function RoutineForm({
  persona,
  routine,
  seed,
  onClose,
}: {
  persona: PersonaId;
  routine?: Routine; // editing this one
  seed?: RoutineSeed; // or a new one, starting from this question
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const id = useId();
  const first = useRef<HTMLInputElement>(null);
  const [name, setName] = useState(routine?.name ?? "");
  const [question, setQuestion] = useState(routine?.question ?? seed?.question ?? "");
  const [sources, setSources] = useState<Source[]>(routine?.sources ?? seed?.sources ?? []);
  const [schedule, setSchedule] = useState<RoutineSchedule>(routine?.schedule ?? { frequency: "weekdays", weekday: null, time: "09:00" });
  const [timezone, setTimezone] = useState(routine?.timezone ?? browserZone());

  useEffect(() => {
    first.current?.focus();
    const escape = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", escape);
    return () => document.removeEventListener("keydown", escape);
  }, [onClose]);

  const save = useMutation({
    mutationFn: () => {
      const draft = { name: name.trim(), question: question.trim(), sources, schedule, timezone: timezone.trim() };
      return routine ? api(persona).updateRoutine(routine.routine_id, draft) : api(persona).createRoutine(draft);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["routines", persona] });
      qc.invalidateQueries({ queryKey: ["mywork", persona] });
      onClose();
    },
  });
  const problem = save.error
    ? save.error instanceof ApiError && save.error.status === 409
      ? "You already have five active routines. Pause or delete one first."
      : `That couldn’t be saved: ${(save.error as Error).message}.`
    : null;
  const ready = name.trim() && question.trim() && timezone.trim() && /^\d\d:\d\d$/.test(schedule.time);

  return (
    <div className="routine-form__scrim" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <form
        className="card routine-form"
        role="dialog"
        aria-modal="true"
        aria-labelledby={`${id}-title`}
        onSubmit={(e) => {
          e.preventDefault();
          if (ready && !save.isPending) save.mutate();
        }}
      >
        <header>
          <h2 id={`${id}-title`} className="h3">
            {routine ? "Edit routine" : "New routine"}
          </h2>
          <button type="button" className="icon-button" aria-label="Close" onClick={onClose}>
            <X size={16} />
          </button>
        </header>
        <p className="muted small">
          Cortex asks this question for you on the schedule, as you, and keeps the answer here. Each run is checked against
          what you can open at that moment.
        </p>

        <label htmlFor={`${id}-name`}>Name</label>
        <input id={`${id}-name`} ref={first} value={name} maxLength={80} placeholder="Morning brief" onChange={(e) => setName(e.target.value)} />

        <label htmlFor={`${id}-q`}>Question</label>
        <textarea id={`${id}-q`} rows={3} value={question} maxLength={2000} onChange={(e) => setQuestion(e.target.value)} />

        <div className="routine-form__row">
          <div>
            <label htmlFor={`${id}-freq`}>Repeats</label>
            <select
              id={`${id}-freq`}
              value={schedule.frequency}
              onChange={(e) => {
                const frequency = e.target.value as RoutineSchedule["frequency"];
                setSchedule({ ...schedule, frequency, weekday: frequency === "weekly" ? (schedule.weekday ?? 0) : null });
              }}
            >
              <option value="daily">Every day</option>
              <option value="weekdays">Every weekday</option>
              <option value="weekly">Once a week</option>
            </select>
          </div>
          {schedule.frequency === "weekly" && (
            <div>
              <label htmlFor={`${id}-day`}>On</label>
              <select id={`${id}-day`} value={schedule.weekday ?? 0} onChange={(e) => setSchedule({ ...schedule, weekday: Number(e.target.value) })}>
                {WEEKDAYS.map((d, i) => (
                  <option key={d} value={i}>
                    {d}
                  </option>
                ))}
              </select>
            </div>
          )}
          <div>
            <label htmlFor={`${id}-time`}>At</label>
            <input id={`${id}-time`} type="time" required value={schedule.time} onChange={(e) => setSchedule({ ...schedule, time: e.target.value })} />
          </div>
          <div>
            <label htmlFor={`${id}-tz`}>Time zone</label>
            <input id={`${id}-tz`} value={timezone} placeholder="Asia/Singapore" onChange={(e) => setTimezone(e.target.value)} />
          </div>
        </div>

        <fieldset>
          <legend>Search in</legend>
          <label>
            <input type="checkbox" checked={sources.length === 0} onChange={() => setSources([])} /> All sources
          </label>
          {SOURCES.map((s) => (
            <label key={s}>
              <input
                type="checkbox"
                checked={sources.includes(s)}
                onChange={() => setSources(sources.includes(s) ? sources.filter((x) => x !== s) : [...sources, s])}
              />{" "}
              {SOURCE_LABEL[s]}
            </label>
          ))}
        </fieldset>

        {problem && (
          <p role="alert" className="banner banner--stale">
            {problem}
          </p>
        )}
        <footer>
          <button type="button" className="ghost" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="routine-form__save" disabled={!ready || save.isPending}>
            {routine ? "Save changes" : "Create routine"}
          </button>
        </footer>
      </form>
    </div>
  );
}

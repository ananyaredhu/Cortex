import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, CalendarClock, Zap } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type { RoutineRun } from "../api/types";
import { AnswerCard } from "../ask/AnswerCard";
import { restored } from "../ask/restored";
import { usePersona } from "../auth/PersonaContext";
import type { PersonaId } from "../auth/personas";
import { outcomeText, pausedText, scheduleText, whenIn } from "./format";
import { Running } from "./Running";
import { useRoutines, useRunNow } from "./useRoutines";
import "../ask/ask.css";
import "../home/home.css";
import "./routines.css";

/**
 * One routine and every run it has had, newest first, with the chosen run's answer beside the list: the chats a routine
 * produced, in one place. The list carries no answer text (api.md 0.3); each answer is read through its conversation, so
 * a run whose sources the person can no longer open is withheld here exactly as it is in Ask.
 */
export function RoutineHistoryPage() {
  const { persona } = usePersona();
  const { routineId = "" } = useParams<{ routineId: string }>();
  const list = useRoutines(persona.id, { watch: true });
  const routine = list.data?.routines?.find((r) => r.routine_id === routineId);
  const history = useQuery({
    queryKey: ["routine-runs", persona.id, routineId],
    queryFn: () => api(persona.id).routineRuns(routineId),
    retry: (count, e) => !(e instanceof ApiError && e.status === 404) && count < 2,
  });
  const { run, isRunning } = useRunNow(persona.id);
  const [picked, setPicked] = useState<string | null>(null);
  const runs = history.data?.runs ?? [];
  // The chosen run, else the newest one that has an answer to open.
  const shown = runs.find((r) => r.request_id === picked) ?? runs.find((r) => r.conversation_id) ?? null;
  const running = routine ? isRunning(routine) : false;

  // Someone else's routine and one that does not exist look the same, here as in the API.
  if ((list.data && !routine) || (history.error instanceof ApiError && history.error.status === 404)) {
    return (
      <div className="page home routines">
        <p role="alert" className="banner banner--stale">
          That routine isn’t available.
        </p>
        <Link className="ghost ghost--accent" to="/routines">
          <ArrowLeft size={14} aria-hidden /> All routines
        </Link>
      </div>
    );
  }

  return (
    <div className="page home routines routine-history">
      <Link className="ghost ghost--accent small routine-history__back" to="/routines">
        <ArrowLeft size={12} aria-hidden /> All routines
      </Link>
      {routine && (
        <header className={`card routines__item${running ? " routines__item--running" : ""}`}>
          <div className="routines__what">
            <h1 className="h3">
              <CalendarClock size={16} aria-hidden /> {routine.name}
              {routine.status === "paused" && <span className="home__status">Paused</span>}
              {running && <Running />}
            </h1>
            <p className="routines__question">{routine.question}</p>
            <p className="muted small">
              {scheduleText(routine.schedule)} <span className="mono">({routine.timezone})</span>
              {routine.next_run_at && <> · next {whenIn(routine.next_run_at, routine.timezone)}</>}
            </p>
            {routine.status === "paused" && <p className="small">{pausedText(routine)}</p>}
          </div>
          <div className="routines__actions">
            <button
              type="button"
              className="ghost"
              disabled={running}
              onClick={() => run.mutate(routine.routine_id, { onSuccess: (answer) => setPicked(answer.request_id) })}
            >
              <Zap size={14} aria-hidden /> {running ? "Running…" : "Run now"}
            </button>
          </div>
        </header>
      )}
      {run.error && (
        <p role="alert" className="banner banner--stale">
          That didn’t work: {(run.error as Error).message}.
        </p>
      )}

      <div className="routine-history__body">
        <section aria-labelledby="h-runs" className="routine-history__runs">
          <h2 id="h-runs" className="eyebrow">
            Runs <span className="mono muted">{runs.length || ""}</span>
          </h2>
          <ol>
            {running && (
              <li className="routine-history__run routine-history__run--now">
                <Running label="Running now: the answer will appear here" />
              </li>
            )}
            {runs.map((r) => (
              <li key={r.request_id}>
                <RunRow run={r} zone={routine?.timezone ?? "UTC"} current={r.request_id === shown?.request_id} onPick={() => setPicked(r.request_id)} />
              </li>
            ))}
          </ol>
          {history.data && runs.length === 0 && !running && <p className="muted small">No runs yet. It runs at its next scheduled time, or try “Run now”.</p>}
          {history.isError && <p className="banner banner--error">{(history.error as Error).message}</p>}
        </section>

        <section aria-label="Answer of the selected run" className="routine-history__answer">
          {shown?.conversation_id ? (
            <RunAnswer key={shown.request_id} persona={persona.id} run={shown} zone={routine?.timezone ?? "UTC"} />
          ) : (
            history.data && !running && <p className="card routines__empty muted">Choose a run to read its answer.</p>
          )}
        </section>
      </div>
    </div>
  );
}

function RunRow({ run, zone, current, onPick }: { run: RoutineRun; zone: string; current: boolean; onPick: () => void }) {
  const label = (
    <>
      <span className="routine-history__when">{whenIn(run.ran_at, zone)}</span>
      <span className="muted small">{outcomeText(run)}</span>
    </>
  );
  // A skipped run asked nothing, so there is nothing to open.
  if (!run.conversation_id) return <span className="routine-history__run routine-history__run--skipped">{label}</span>;
  return (
    <button type="button" className={`routine-history__run${current ? " routine-history__run--on" : ""}`} aria-current={current || undefined} onClick={onPick}>
      {label}
    </button>
  );
}

/** One run's answer, read through its conversation: the withholding rule is the conversation's own. */
function RunAnswer({ persona, run, zone }: { persona: PersonaId; run: RoutineRun; zone: string }) {
  const navigate = useNavigate();
  const id = run.conversation_id!;
  const conversation = useQuery({ queryKey: ["conversation", persona, id], queryFn: () => api(persona).conversation(id) });
  const turn = conversation.data?.turns.find((t) => t.request_id === run.request_id) ?? conversation.data?.turns[0];
  return (
    <>
      <div className="routine-history__answer-head">
        <span className="small">{whenIn(run.ran_at, zone)}</span>
        <Link className="ghost ghost--accent small" to={`/ask/${encodeURIComponent(id)}`}>
          Open in Ask <ArrowRight size={12} aria-hidden />
        </Link>
      </div>
      {conversation.isPending && (
        <p className="footnote" role="status">
          Opening the run…
        </p>
      )}
      {conversation.isError && <p className="banner banner--stale">This run isn’t available.</p>}
      {turn &&
        (turn.withheld ? (
          <p className="banner banner--stale">
            This answer isn’t shown again: something it relied on is no longer available to you. Run the routine again for a
            fresh answer.
          </p>
        ) : (
          <AnswerCard asker={persona} answer={restored(turn, id)} pinnable onChoose={(q) => navigate("/ask", { state: { ask: q } })} />
        ))}
    </>
  );
}

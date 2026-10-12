import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, CalendarClock, Pause, Pencil, Play, Plus, Trash2, Zap } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { Routine } from "../api/types";
import { usePersona } from "../auth/PersonaContext";
import { outcomeText, pausedText, scheduleText, whenIn } from "./format";
import { RoutineForm } from "./RoutineForm";
import "../home/home.css";
import "./routines.css";

/**
 * A person's own routines (docs/routines.md): questions Cortex asks again on a schedule, as them. Nothing here is about
 * anyone else's routines, and a run's answer is not on this page: it opens in Ask, under the same rule as any reopened
 * conversation.
 */
export function RoutinesPage() {
  const { persona } = usePersona();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const key = ["routines", persona.id];
  const list = useQuery({ queryKey: key, queryFn: () => api(persona.id).routines() });
  const [form, setForm] = useState<Routine | "new" | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const refresh = () => {
    qc.invalidateQueries({ queryKey: key });
    qc.invalidateQueries({ queryKey: ["mywork", persona.id] });
  };
  const change = useMutation({
    mutationFn: ({ id, status }: { id: string; status: "active" | "paused" }) => api(persona.id).updateRoutine(id, { status }),
    onSettled: refresh,
  });
  const remove = useMutation({ mutationFn: (id: string) => api(persona.id).deleteRoutine(id), onSettled: refresh });
  const run = useMutation({
    mutationFn: (id: string) => api(persona.id).runRoutine(id),
    onSuccess: (answer) => {
      refresh();
      qc.invalidateQueries({ queryKey: ["conversations", persona.id] });
      navigate(`/ask/${encodeURIComponent(answer.conversation_id)}`);
    },
  });
  const routines = list.data?.routines ?? [];
  const failed = change.error ?? remove.error ?? run.error;

  return (
    <div className="page home routines">
      <p className="eyebrow">Routines</p>
      <h1 className="display">
        Questions Cortex asks for you.
        <br />
        <strong>On a schedule, as you.</strong>
      </h1>
      <div className="routines__bar">
        <p className="muted small">
          Each run is an ordinary question: it sees only what you can open at that moment, and it is logged like any other. Up to
          five can be active.
        </p>
        <button type="button" className="chip routines__new" onClick={() => setForm("new")}>
          <Plus size={14} aria-hidden /> New routine
        </button>
      </div>
      {list.isError && <p className="banner banner--error">{(list.error as Error).message}</p>}
      {failed && (
        <p role="alert" className="banner banner--stale">
          That didn’t work: {(failed as Error).message}.
        </p>
      )}
      {list.data && routines.length === 0 && (
        <p className="card routines__empty muted">
          No routines yet. Try “Summarise what needs my focus today” every weekday at 09:00.
        </p>
      )}

      <ul className="routines__list">
        {routines.map((r) => (
          <li key={r.routine_id} className="card routines__item" aria-label={r.name}>
            <div className="routines__what">
              <h2 className="h3">
                <CalendarClock size={16} aria-hidden /> {r.name}
                {r.status === "paused" && <span className="home__status">Paused</span>}
              </h2>
              <p className="routines__question">“{r.question}”</p>
              <p className="muted small">
                {scheduleText(r.schedule)} <span className="mono">({r.timezone})</span>
                {r.next_run_at && <> · next {whenIn(r.next_run_at, r.timezone)}</>}
              </p>
              {r.status === "paused" && <p className="small">{pausedText(r)}</p>}
              {r.last_run && (
                <p className="small routines__last">
                  {r.last_run.unread && <span className="routines__dot" role="img" aria-label="Unread" />}
                  Last run {whenIn(r.last_run.ran_at, r.timezone)}: {outcomeText(r.last_run)}.
                  {r.last_run.conversation_id && (
                    <Link className="ghost ghost--accent small" to={`/ask/${encodeURIComponent(r.last_run.conversation_id)}`}>
                      Open <ArrowRight size={12} aria-hidden />
                    </Link>
                  )}
                </p>
              )}
            </div>
            <div className="routines__actions">
              <button type="button" className="ghost" disabled={run.isPending} onClick={() => run.mutate(r.routine_id)}>
                <Zap size={14} aria-hidden /> Run now
              </button>
              <button
                type="button"
                className="ghost"
                onClick={() => change.mutate({ id: r.routine_id, status: r.status === "active" ? "paused" : "active" })}
              >
                {r.status === "active" ? <Pause size={14} aria-hidden /> : <Play size={14} aria-hidden />}
                {r.status === "active" ? "Pause" : "Resume"}
              </button>
              <button type="button" className="ghost" onClick={() => setForm(r)}>
                <Pencil size={14} aria-hidden /> Edit
              </button>
              {confirming === r.routine_id ? (
                <button
                  type="button"
                  className="ghost routines__confirm"
                  onClick={() => {
                    setConfirming(null);
                    remove.mutate(r.routine_id);
                  }}
                  onBlur={() => setConfirming(null)}
                >
                  <Trash2 size={14} aria-hidden /> Really delete?
                </button>
              ) : (
                <button type="button" className="ghost" onClick={() => setConfirming(r.routine_id)}>
                  <Trash2 size={14} aria-hidden /> Delete
                </button>
              )}
            </div>
          </li>
        ))}
      </ul>

      {form && <RoutineForm persona={persona.id} routine={form === "new" ? undefined : form} onClose={() => setForm(null)} />}
    </div>
  );
}

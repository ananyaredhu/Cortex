import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, CalendarClock, Clock, Pause, Pencil, Play, Plus, Trash2, Zap } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type { Routine } from "../api/types";
import { usePersona } from "../auth/PersonaContext";
import { outcomeText, pausedText, scheduleText, whenIn } from "./format";
import { RoutineForm, type RoutineSeed } from "./RoutineForm";
import { TEMPLATES } from "./templates";
import "../home/home.css";
import "./routines.css";

/**
 * A person's own routines (docs/routines.md): instructions Cortex follows again on a schedule, as them, and templates to
 * start one from. Nothing here is about anyone else's routines, and a run's answer is not on this page: it opens in Ask,
 * under the same rule as any reopened conversation.
 */
export function RoutinesPage() {
  const { persona } = usePersona();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const key = ["routines", persona.id];
  const list = useQuery({ queryKey: key, queryFn: () => api(persona.id).routines() });
  const [tab, setTab] = useState<"yours" | "templates">("yours");
  const [form, setForm] = useState<Routine | RoutineSeed | null>(null); // a routine being edited, or what a new one starts from
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
        Work Cortex does for you.
        <br />
        <strong>On a schedule, as you.</strong>
      </h1>
      <p className="muted small routines__intro">
        Write the instructions once and Cortex follows them on the schedule. Each run is an ordinary question: it sees only what
        you can open at that moment, and it is logged like any other. Up to five can be active.
      </p>
      <div className="routines__bar">
        <div className="routines__tabs" role="tablist" aria-label="Routines">
          <button type="button" role="tab" id="tab-yours" aria-selected={tab === "yours"} aria-controls="panel-routines" onClick={() => setTab("yours")}>
            Yours
          </button>
          <button type="button" role="tab" id="tab-templates" aria-selected={tab === "templates"} aria-controls="panel-routines" onClick={() => setTab("templates")}>
            Templates
          </button>
        </div>
        <button type="button" className="chip routines__new" onClick={() => setForm({})}>
          <Plus size={14} aria-hidden /> New routine
        </button>
      </div>

      <div id="panel-routines" role="tabpanel" aria-labelledby={`tab-${tab}`} className="routines__panel">
        {tab === "templates" ? (
          <>
            <p className="muted small">Pick one to start from. You can change every field before it is saved.</p>
            <ul className="routines__templates">
              {TEMPLATES.map((t) => (
                <li key={t.id}>
                  <button
                    type="button"
                    className="card routines__template"
                    onClick={() => setForm({ name: t.name, question: t.instructions, sources: t.sources, schedule: t.schedule })}
                  >
                    <span className="routines__template-icon">
                      <t.icon size={18} aria-hidden />
                    </span>
                    <span className="routines__template-text">
                      <span className="routines__template-name">{t.name}</span>
                      <span className="routines__template-what">{t.description}</span>
                      <span className="muted small routines__template-when">
                        <Clock size={13} aria-hidden /> {scheduleText(t.schedule)}
                      </span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </>
        ) : (
          <>
            {list.isError && (
              <p className="banner banner--error">
                {list.error instanceof ApiError && list.error.status === 404
                  ? "This Brain does not serve routines yet. Update it and restart, then reload this page."
                  : `Your routines could not be loaded: ${(list.error as Error).message}.`}
              </p>
            )}
            {failed && (
              <p role="alert" className="banner banner--stale">
                That didn’t work: {(failed as Error).message}.
              </p>
            )}
            {list.data && routines.length === 0 && (
              <div className="card routines__empty">
                <CalendarClock size={22} aria-hidden />
                <h2 className="h3">You have no routines yet</h2>
                <p className="muted small">
                  Create one to have Cortex do a piece of work for you on a schedule, such as a morning brief every weekday at 09:00.
                </p>
                <div className="routines__empty-actions">
                  <button type="button" className="routine-form__save" onClick={() => setForm({})}>
                    Create your first routine
                  </button>
                  <button type="button" className="chip" onClick={() => setTab("templates")}>
                    Start from a template
                  </button>
                </div>
              </div>
            )}

            <ul className="routines__list">
              {routines.map((r) => (
                <li key={r.routine_id} className="card routines__item" aria-label={r.name}>
                  <div className="routines__what">
                    <h2 className="h3">
                      <CalendarClock size={16} aria-hidden /> {r.name}
                      {r.status === "paused" && <span className="home__status">Paused</span>}
                    </h2>
                    <p className="routines__question">{r.question}</p>
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
          </>
        )}
      </div>

      {form &&
        ("routine_id" in form ? (
          <RoutineForm persona={persona.id} routine={form} onClose={() => setForm(null)} />
        ) : (
          <RoutineForm
            persona={persona.id}
            seed={form}
            onClose={() => setForm(null)}
            onSaved={() => setTab("yours")} // a routine made from a template shows up under Yours
          />
        ))}
    </div>
  );
}

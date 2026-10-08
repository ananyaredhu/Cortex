import { useState } from "react";
import { api } from "../api/client";
import type { AskResponse, Source } from "../api/types";
import { AnswerCard } from "../ask/AnswerCard";
import { Composer } from "../ask/Composer";
import { Stepper, type StageTimes } from "../ask/Stepper";
import { PERSONAS, type PersonaId } from "../auth/personas";
import { Avatar } from "../components/Avatar";
import "../ask/ask.css";
import "./compare.css";

interface Column {
  persona: PersonaId;
  times: StageTimes;
  answer?: AskResponse;
  error?: string;
}

const DEFAULT: PersonaId[] = ["priya", "sam", "dana"];

/**
 * Split-screen demo: the same question asked by several people at once. Each column is its own request
 * with its own persona's token, and its answer is rendered only in that column: nothing is shared or
 * merged across columns in the browser.
 */
export function ComparePage() {
  const [columns, setColumns] = useState<Column[]>(DEFAULT.map((persona) => ({ persona, times: {} })));
  const [question, setQuestion] = useState<string | null>(null);

  const update = (i: number, f: (c: Column) => Column) => setColumns((cs) => cs.map((c, j) => (j === i ? f(c) : c)));

  const ask = (q: string, sources: Source[]) => {
    setQuestion(q);
    setColumns((cs) => cs.map((c) => ({ persona: c.persona, times: {} })));
    columns.forEach((c, i) => {
      api(c.persona)
        .askStream({ question: q, ...(sources.length ? { sources } : {}) }, (stage, status) =>
          update(i, (col) => ({
            ...col,
            times: {
              ...col.times,
              [stage]:
                status === "start" ? { start: performance.now() } : { ...col.times[stage]!, end: performance.now() },
            },
          })),
        )
        .then((answer) => update(i, (col) => ({ ...col, answer })))
        .catch((e: Error) => update(i, (col) => ({ ...col, error: e.message })));
    });
  };

  const busy = question !== null && columns.some((c) => !c.answer && !c.error);

  return (
    <div className="compare">
      <header className="compare__head">
        <h1 className="h2">Same question, different people</h1>
        <p className="muted small">
          Each column asks as that person, with their own access. Pick who sits in each column, then ask once.
        </p>
      </header>

      <div className="compare__grid">
        {columns.map((c, i) => {
          const p = PERSONAS.find((x) => x.id === c.persona)!;
          return (
            <section key={i} className="compare__col" aria-label={`Answer for ${p.name}`}>
              <div className="compare__who">
                <Avatar src={p.avatar} name={p.name} size={32} />
                <label className="visually-hidden" htmlFor={`col-${i}`}>
                  Column {i + 1} person
                </label>
                <select
                  id={`col-${i}`}
                  value={c.persona}
                  disabled={busy}
                  onChange={(e) => update(i, () => ({ persona: e.target.value as PersonaId, times: {} }))}
                >
                  {PERSONAS.map((x) => (
                    <option key={x.id} value={x.id}>
                      {x.name} · {x.role}
                    </option>
                  ))}
                </select>
                {p.guest && <span className="guest-badge">Guest</span>}
              </div>
              {question === null ? (
                <p className="muted small compare__waiting">Waiting for a question.</p>
              ) : c.answer ? (
                <AnswerCard asker={c.persona} answer={c.answer} onChoose={(q) => ask(q, [])} />
              ) : c.error ? (
                <p role="alert" className="banner banner--error">
                  Couldn’t answer ({c.error}).
                </p>
              ) : (
                <Stepper times={c.times} />
              )}
            </section>
          );
        })}
      </div>

      <div className="compare__composer">
        {question && <p className="compare__q">“{question}”</p>}
        <Composer busy={busy} onAsk={ask} />
      </div>
    </div>
  );
}

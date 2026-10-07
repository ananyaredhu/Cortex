import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowUp, Check } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import { STAGES, type AskResponse, type Stage } from "../api/types";
import { usePersona } from "../auth/PersonaContext";
import { SourceGlyph, SOURCE_LABEL } from "../components/SourceGlyph";

// Phase 0 wiring proof: question in, real pipeline stages, answer and citations out.
// Phase 1 replaces the answer block with the answer card and Trust panel from
// design-reference/screens/Ask.dc.html, re-skinned to Cortex.
const STAGE_LABEL: Record<Stage, string> = {
  retrieve: "Finding sources",
  authorize: "Checking your access",
  verify_live: "Verifying with source systems",
  generate: "Writing answer",
  check: "Checking citations",
};

export function Ask() {
  const { persona } = usePersona();
  const qc = useQueryClient();
  const [question, setQuestion] = useState("");
  const [stages, setStages] = useState<Partial<Record<Stage, "start" | "done">>>({});

  const ask = useMutation({
    mutationFn: (q: string) => {
      setStages({});
      return api(persona.id).askStream({ question: q }, (stage, status) =>
        setStages((s) => ({ ...s, [stage]: status })),
      );
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["conversations", persona.id] }),
  });

  const submit = () => {
    if (question.trim()) ask.mutate(question.trim());
  };

  return (
    <div className="page page--reading">
      {persona.guest && <div className="guest-badge">{persona.guest}</div>}
      <h1 className="display">
        What do you need to <strong>know?</strong>
      </h1>
      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <label htmlFor="question" className="visually-hidden">
          Question
        </label>
        <textarea
          id="question"
          rows={3}
          value={question}
          placeholder="Ask across Confluence, Jira, Slack and Drive…"
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit();
          }}
        />
        <button type="submit" aria-label="Ask" disabled={ask.isPending || !question.trim()}>
          <ArrowUp size={18} />
        </button>
      </form>
      <p className="footnote">Cortex never shows what you couldn’t open yourself. Every question is logged.</p>

      {ask.isPending && (
        <ol className="stages" aria-live="polite">
          {STAGES.map((s) => (
            <li key={s} data-state={stages[s] ?? "waiting"}>
              {stages[s] === "done" && <Check size={14} aria-hidden />} {STAGE_LABEL[s]}
            </li>
          ))}
        </ol>
      )}
      {ask.isError && <p role="alert">Something went wrong: {(ask.error as Error).message}</p>}
      {ask.data && <AnswerPreview answer={ask.data} />}
    </div>
  );
}

function AnswerPreview({ answer }: { answer: AskResponse }) {
  return (
    <article className="card answer" aria-label="Answer">
      <p className="answer__text">{answer.answer}</p>
      {answer.citations.length > 0 && (
        <ol className="answer__sources">
          {answer.citations.map((c) => (
            <li key={c.doc_id}>
              <SourceGlyph source={c.source} />
              <a href={c.url} target="_blank" rel="noreferrer">
                {c.title}
              </a>
              <span className="mono muted">
                {SOURCE_LABEL[c.source]} · as of {new Date(c.as_of).toLocaleString()}
              </span>
            </li>
          ))}
        </ol>
      )}
      <footer className="mono muted">
        {answer.request_id} · recorded in the audit log
        {answer.policy_version && ` · policy ${answer.policy_version}`}
      </footer>
    </article>
  );
}

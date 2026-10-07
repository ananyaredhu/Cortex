import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Check, Minus } from "lucide-react";
import { api } from "../api/client";
import { usePersona } from "../auth/PersonaContext";

export function EmptyState({ onAsk }: { onAsk: (question: string) => void }) {
  const { persona } = usePersona();
  // Suggested questions come from /mywork, so they are the persona's own and pass the PDP.
  const work = useQuery({ queryKey: ["mywork", persona.id], queryFn: () => api(persona.id).mywork() });

  return (
    <div className="empty">
      <h1 className="display">
        {greeting()}, {persona.name}.<br />
        <strong>What do you need to know?</strong>
      </h1>
      <p className="empty__lede">
        Ask one question across Company A’s tools. Every answer is cited, shows how fresh its sources are, and is
        built only from what you can already open yourself.
      </p>

      {!!work.data?.suggested_questions.length && (
        <ul className="empty__examples" aria-label="Try asking">
          {work.data.suggested_questions.map((q) => (
            <li key={q}>
              <button type="button" onClick={() => onAsk(q)}>
                <span>{q}</span>
                <ArrowRight size={16} aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="empty__scope">
        <div className="card empty__box">
          <h2 className="h3">
            <Check size={16} aria-hidden /> What I can see
          </h2>
          <ul>
            <li>Pages, tickets, messages and files you can already open.</li>
            <li>Changes up to the last sync, which every answer shows.</li>
          </ul>
        </div>
        <div className="card empty__box">
          <h2 className="h3">
            <Minus size={16} aria-hidden /> What I can’t see
          </h2>
          <ul>
            <li>Anything you don’t have access to. I won’t name it, quote it or hint that it exists.</li>
            <li>Every question you ask is recorded in the audit log.</li>
          </ul>
        </div>
      </div>
    </div>
  );
}

function greeting(): string {
  const h = new Date().getHours();
  return h < 12 ? "Morning" : h < 18 ? "Afternoon" : "Evening";
}

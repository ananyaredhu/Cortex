import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight } from "lucide-react";
import { api } from "../api/client";
import { usePersona } from "../auth/PersonaContext";

/** A new question starts with a greeting and nothing else; the composer sits right under it (ui/MOCKUP-DRIFT.md #52). */
export function EmptyState() {
  const { persona } = usePersona();
  return (
    <div className="empty">
      <h1 className="display">
        {greeting()}, {persona.name}.<br />
        <strong>What do you need to know?</strong>
      </h1>
    </div>
  );
}

/** The persona's own suggested questions, as quiet links under the composer. */
export function Suggestions({ onAsk }: { onAsk: (question: string) => void }) {
  const { persona } = usePersona();
  // Suggested questions come from /mywork, so they are the persona's own and pass the PDP.
  const work = useQuery({ queryKey: ["mywork", persona.id], queryFn: () => api(persona.id).mywork() });
  if (!work.data?.suggested_questions.length) return null;
  return (
    <ul className="empty__examples" aria-label="Try asking">
      {work.data.suggested_questions.map((q) => (
        <li key={q}>
          <button type="button" onClick={() => onAsk(q)}>
            <ArrowUpRight size={14} aria-hidden />
            <span>{q}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}

function greeting(): string {
  const h = new Date().getHours();
  return h < 12 ? "Morning" : h < 18 ? "Afternoon" : "Evening";
}

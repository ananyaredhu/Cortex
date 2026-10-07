import { useQueryClient } from "@tanstack/react-query";
import { PanelRight } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { api } from "../api/client";
import type { AskResponse, Source } from "../api/types";
import { usePersona } from "../auth/PersonaContext";
import { Avatar } from "../components/Avatar";
import { AnswerCard } from "./AnswerCard";
import { Composer } from "./Composer";
import { EmptyState } from "./EmptyState";
import { Stepper, type StageTimes } from "./Stepper";
import { TrustPanel } from "./TrustPanel";
import "./ask.css";

interface Turn {
  id: number;
  question: string;
  times: StageTimes;
  answer?: AskResponse;
  error?: string;
}

const TRUST_KEY = "cortex.trustOpen";

function initialTrustOpen(): boolean {
  try {
    return localStorage.getItem(TRUST_KEY) !== "0";
  } catch {
    return true;
  }
}

export function AskPage() {
  const { persona } = usePersona();
  const location = useLocation();
  const qc = useQueryClient();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [conversationId, setConversationId] = useState<string | undefined>();
  const [selected, setSelected] = useState<number | null>(null);
  const [trustOpen, setTrustOpen] = useState(initialTrustOpen);
  const [whyLit, setWhyLit] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);
  const nextId = useRef(1);

  // A new persona or "Ask Cortex" starts a new conversation: never show one persona's answers to another.
  useEffect(() => {
    setTurns([]);
    setConversationId(undefined);
    setSelected(null);
  }, [persona.id, location.key]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);

  const busy = turns.some((t) => !t.answer && !t.error);
  const update = (id: number, f: (t: Turn) => Turn) => setTurns((ts) => ts.map((t) => (t.id === id ? f(t) : t)));

  const ask = async (question: string, sources: Source[] = []) => {
    const id = nextId.current++;
    const asker = persona.id;
    setTurns((ts) => [...ts, { id, question, times: {} }]);
    setSelected(id);
    setWhyLit(false);
    try {
      const answer = await api(asker).askStream(
        { question, conversation_id: conversationId, ...(sources.length ? { sources } : {}) },
        (stage, status) =>
          update(id, (t) => ({
            ...t,
            times: {
              ...t.times,
              [stage]:
                status === "start" ? { start: performance.now() } : { ...t.times[stage]!, end: performance.now() },
            },
          })),
      );
      update(id, (t) => ({ ...t, answer }));
      setConversationId(answer.conversation_id);
      qc.invalidateQueries({ queryKey: ["conversations", asker] });
    } catch (e) {
      update(id, (t) => ({ ...t, error: (e as Error).message }));
    }
  };

  const shown = turns.find((t) => t.id === selected)?.answer ?? null;
  const openTrust = (open: boolean) => {
    setTrustOpen(open);
    try {
      localStorage.setItem(TRUST_KEY, open ? "1" : "0");
    } catch {
      // not remembered; fine
    }
  };

  return (
    <div className={`ask${trustOpen ? " ask--trust" : ""}`}>
      <div className="ask__main">
        <header className="ask__head">
          <span className="ask__title">{turns[0]?.question ?? "New question"}</span>
          {persona.guest && <span className="guest-badge">{persona.guest}</span>}
          {!trustOpen && (
            <button type="button" className="ghost" aria-expanded={false} onClick={() => openTrust(true)}>
              <PanelRight size={16} aria-hidden /> Trust panel
            </button>
          )}
        </header>

        <div className="ask__thread">
          {turns.length === 0 ? (
            <EmptyState onAsk={(q) => ask(q)} />
          ) : (
            turns.map((t) => (
              <section
                key={t.id}
                className={`turn${t.id === selected ? " turn--selected" : ""}`}
                onClick={() => t.answer && setSelected(t.id)}
              >
                <div className="turn__q">
                  <Avatar src={persona.avatar} name={persona.name} size={28} />
                  <p>{t.question}</p>
                </div>
                {t.answer ? (
                  <AnswerCard
                    answer={t.answer}
                    onChoose={(q) => ask(q)}
                    onWhyMore={() => {
                      setSelected(t.id);
                      openTrust(true);
                      setWhyLit(true);
                    }}
                  />
                ) : t.error ? (
                  <p role="alert" className="banner banner--error">
                    Cortex couldn’t answer right now ({t.error}). Try again in a moment.
                  </p>
                ) : (
                  <Stepper times={t.times} />
                )}
              </section>
            ))
          )}
          <div ref={endRef} />
        </div>

        <div className="ask__composer">
          <Composer busy={busy} onAsk={ask} />
          <p className="footnote">Cortex never shows what you couldn’t open yourself. Every question is logged.</p>
        </div>
      </div>

      {trustOpen && <TrustPanel answer={shown} highlightWhy={whyLit} onClose={() => openTrust(false)} />}
    </div>
  );
}


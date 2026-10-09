import { useQueryClient } from "@tanstack/react-query";
import { PanelRight } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useLocation, useParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type { AskResponse, ConversationTurn, Source } from "../api/types";
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
  /** A reopened answer the asker may no longer see: a cited document was revoked since (api.md, conversations). */
  withheld?: boolean;
}

/** "idle": a live chat. "loading": fetching a reopened conversation. "missing": not the caller's, or gone. */
type Load = "idle" | "loading" | "missing";

const TRUST_KEY = "cortex.trustOpen";

function initialTrustOpen(): boolean {
  // On phones the panel is a bottom sheet over the answer: start closed and open it from an answer's pill.
  if (window.matchMedia?.("(max-width: 720px)").matches) return false;
  try {
    return localStorage.getItem(TRUST_KEY) !== "0";
  } catch {
    return true;
  }
}

/** A turn from the audit log as the answer card expects it: what the log holds, nothing invented. */
function restored(t: ConversationTurn, conversationId: string): AskResponse {
  return {
    request_id: t.request_id,
    conversation_id: conversationId,
    answer: t.answer ?? "",
    claims: [],
    citations: t.citations,
    refused: t.refused,
    abstained: t.abstained,
    skill: t.skill ?? null,
    grounding: null,
  };
}

export function AskPage() {
  const { persona } = usePersona();
  const location = useLocation();
  const { conversationId: routeId } = useParams<{ conversationId: string }>();
  const qc = useQueryClient();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [conversationId, setConversationId] = useState<string | undefined>();
  const [load, setLoad] = useState<Load>("idle");
  const [selected, setSelected] = useState<number | null>(null);
  const [trustOpen, setTrustOpen] = useState(initialTrustOpen);
  const [whyLit, setWhyLit] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);
  const nextId = useRef(1);

  // A new persona, "Ask Cortex" or a sidebar entry starts afresh: never show one persona's answers to another.
  // With a conversation in the URL the thread is reopened from the server, which applies the ownership rule.
  // The refs make each reset and each "ask again" happen once per navigation, even when effects run twice.
  const resetFor = useRef("");
  const askedFor = useRef("");
  useEffect(() => {
    const key = `${persona.id}|${location.key}`;
    if (resetFor.current === key) return;
    resetFor.current = key;
    setTurns([]);
    setConversationId(routeId);
    setSelected(null);
    setLoad(routeId ? "loading" : "idle");
    if (!routeId) return;
    const asker = persona.id;
    api(asker)
      .conversation(routeId)
      .then((c) => {
        if (resetFor.current !== key) return; // navigated away meanwhile
        const loaded: Turn[] = c.turns.map((t) => ({
          id: nextId.current++,
          question: t.question,
          times: {},
          answer: t.withheld ? undefined : restored(t, c.conversation_id),
          withheld: t.withheld,
        }));
        setTurns(loaded);
        setSelected([...loaded].reverse().find((t) => t.answer)?.id ?? null);
        setLoad("idle");
      })
      .catch((e) => {
        if (resetFor.current !== key) return;
        setLoad(e instanceof ApiError && e.status === 404 ? "missing" : "idle");
        if (!(e instanceof ApiError && e.status === 404)) {
          setTurns([{ id: nextId.current++, question: "", times: {}, error: (e as Error).message }]);
        }
      });
  }, [persona.id, location.key, routeId]);

  useEffect(() => {
    // Only once there is something to scroll to: scrolling on first render moves the browser's Tab starting
    // point past the skip link.
    if (turns.length) endRef.current?.scrollIntoView?.({ behavior: "smooth", block: "end" });
  }, [turns]);

  const busy = load === "loading" || turns.some((t) => !t.answer && !t.error && !t.withheld);
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

  // Arriving from My Work with a question ("Ask again", a suggestion) asks it straight away.
  useEffect(() => {
    const q = (location.state as { ask?: string } | null)?.ask;
    if (!q || askedFor.current === location.key) return;
    askedFor.current = location.key;
    ask(q);
  }, [location.key]);

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
          <span className="ask__title">{turns[0]?.question || "New question"}</span>
          {persona.guest && <span className="guest-badge">{persona.guest}</span>}
          {!trustOpen && (
            <button type="button" className="ghost" aria-expanded={false} onClick={() => openTrust(true)}>
              <PanelRight size={16} aria-hidden /> Trust panel
            </button>
          )}
        </header>

        <div className="ask__thread">
          {load === "loading" ? (
            <p className="footnote" role="status">
              Opening the conversation…
            </p>
          ) : turns.length === 0 ? (
            <>
              {load === "missing" && (
                <p role="alert" className="banner banner--stale">
                  That conversation isn’t available. Start a new question below.
                </p>
              )}
              <EmptyState onAsk={(q) => ask(q)} />
            </>
          ) : (
            turns.map((t) => (
              <section
                key={t.id}
                className={`turn${t.id === selected ? " turn--selected" : ""}`}
                onClick={() => t.answer && setSelected(t.id)}
              >
                {t.question && (
                  <div className="turn__q">
                    <Avatar src={persona.avatar} name={persona.name} size={28} />
                    <p>{t.question}</p>
                  </div>
                )}
                {t.answer ? (
                  <AnswerCard
                    asker={persona.id}
                    answer={t.answer}
                    onChoose={(q) => ask(q)}
                    onTrust={() => {
                      setSelected(t.id);
                      setTrustOpen(true);
                    }}
                    onWhyMore={() => {
                      setSelected(t.id);
                      openTrust(true);
                      setWhyLit(true);
                    }}
                  />
                ) : t.withheld ? (
                  <p className="banner banner--stale">
                    This answer isn’t shown again: something it relied on is no longer available to you. Ask again
                    for a fresh answer.
                  </p>
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

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, PanelRight } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type { AskResponse, Source } from "../api/types";
import { usePersona } from "../auth/PersonaContext";
import { Avatar } from "../components/Avatar";
import { whenIn } from "../routines/format";
import { RoutineForm, type RoutineSeed } from "../routines/RoutineForm";
import { useRoutines } from "../routines/useRoutines";
import { AnswerCard } from "./AnswerCard";
import { Composer } from "./Composer";
import { EmptyState, Suggestions } from "./EmptyState";
import { recorded, restored } from "./restored";
import { Stepper, type StageTimes } from "./Stepper";
import { TrustPanel } from "./TrustPanel";
import "./ask.css";

interface Turn {
  id: number;
  question: string;
  times: StageTimes;
  answer?: AskResponse;
  /** A reopened answer as the Trust panel shows it: with the trust signals recorded when it was answered. */
  recorded?: AskResponse;
  error?: string;
  /** A reopened answer the asker may no longer see: a cited document was revoked since (api.md, conversations). */
  withheld?: boolean;
  /** A reopened turn that was a routine's run (api.md 0.3), and when it ran. */
  routineId?: string | null;
  askedAt?: string;
  /** What was searched, so "Make this a routine" keeps the same scope. */
  sources?: Source[];
}

/** "idle": a live chat. "loading": fetching a reopened conversation. */
type Load = "idle" | "loading";

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

export function AskPage() {
  const { persona } = usePersona();
  const location = useLocation();
  const navigate = useNavigate();
  const { conversationId: routeId } = useParams<{ conversationId: string }>();
  const qc = useQueryClient();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [conversationId, setConversationId] = useState<string | undefined>();
  const [load, setLoad] = useState<Load>("idle");
  const [unavailable, setUnavailable] = useState(false);
  const [selected, setSelected] = useState<number | null>(null);
  const [trustOpen, setTrustOpen] = useState(initialTrustOpen);
  const [whyLit, setWhyLit] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);
  const nextId = useRef(1);

  // A new persona, "Ask" in the sidebar or a recent question starts afresh: never show one persona's answers to another.
  // With a conversation in the URL the thread is reopened from the server, which applies the ownership rule.
  // The refs make each reset and each "ask again" happen once per navigation, even when effects run twice.
  const resetFor = useRef("");
  const askedFor = useRef("");
  const personaFor = useRef(persona.id);
  useEffect(() => {
    const key = `${persona.id}|${location.key}`;
    if (resetFor.current === key) return;
    resetFor.current = key;
    const switched = personaFor.current !== persona.id;
    personaFor.current = persona.id;
    setTurns([]);
    setSelected(null);
    // The notice is for a link the person opened, carried over the redirect below; a persona switch never shows it.
    setUnavailable(!switched && Boolean((location.state as { unavailable?: boolean } | null)?.unavailable));
    if (routeId && switched) {
      // The conversation in the address was the previous persona's: start the new one clean, without asking for it.
      setConversationId(undefined);
      setLoad("idle");
      navigate("/ask", { replace: true });
      return;
    }
    setConversationId(routeId);
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
          recorded: t.withheld ? undefined : recorded(t, c.conversation_id),
          withheld: t.withheld,
          routineId: t.routine_id,
          askedAt: t.asked_at,
        }));
        setTurns(loaded);
        setSelected([...loaded].reverse().find((t) => t.answer)?.id ?? null);
        setLoad("idle");
      })
      .catch((e) => {
        if (resetFor.current !== key) return;
        setLoad("idle");
        if (e instanceof ApiError && e.status === 404) {
          // Not the caller's, or gone (the two look the same): a new question with the address to match, and one
          // notice so an opened link does not fail silently. The dead id is not kept.
          navigate("/ask", { replace: true, state: { unavailable: true } });
          return;
        }
        setTurns([{ id: nextId.current++, question: "", times: {}, error: (e as Error).message }]);
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
    setTurns((ts) => [...ts, { id, question, times: {}, sources }]);
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

  // The chat's name, once it has one (a generated title or the person's own); the same query the sidebar keeps fresh.
  const named = useQuery({ queryKey: ["conversations", persona.id], queryFn: () => api(persona.id).conversations() });
  const title = named.data?.conversations.find((c) => c.conversation_id === conversationId)?.title;
  // The person's routines, to say which one produced a reopened run; asked for only when a turn is one.
  const [routineSeed, setRoutineSeed] = useState<RoutineSeed | null>(null);
  const fromRoutine = turns.some((t) => t.routineId);
  const routines = useRoutines(persona.id, { enabled: fromRoutine });
  const routineOf = (id: string) => routines.data?.routines.find((r) => r.routine_id === id);
  const chosen = turns.find((t) => t.id === selected);
  const shown = chosen?.recorded ?? chosen?.answer ?? null;
  // Nothing asked yet: the greeting, the composer and the suggestions sit together in the middle of the page.
  const fresh = turns.length === 0 && load !== "loading";
  // The Trust panel is about an answer, so it waits for the first question.
  const trust = trustOpen && !fresh;
  const openTrust = (open: boolean) => {
    setTrustOpen(open);
    try {
      localStorage.setItem(TRUST_KEY, open ? "1" : "0");
    } catch {
      // not remembered; fine
    }
  };

  return (
    <div className={`ask${trust ? " ask--trust" : ""}`}>
      <div className={`ask__main${fresh ? " ask__main--fresh" : ""}`}>
        <header className="ask__head">
          <span className="ask__title">{(turns.length > 0 && title) || turns[0]?.question || "New question"}</span>
          {persona.guest && <span className="guest-badge">{persona.guest}</span>}
          {!trustOpen && !fresh && (
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
              {unavailable && (
                <p role="alert" className="banner banner--stale">
                  That conversation isn’t available. Start a new question below.
                </p>
              )}
              <EmptyState />
            </>
          ) : (
            turns.map((t) => (
              <section
                key={t.id}
                className={`turn${t.id === selected ? " turn--selected" : ""}`}
                onClick={() => t.answer && setSelected(t.id)}
              >
                {t.routineId && (
                  <p className="turn__routine">
                    <CalendarClock size={13} aria-hidden /> From your routine
                    {routineOf(t.routineId) ? ` “${routineOf(t.routineId)!.name}”` : ""}
                    {t.askedAt ? ` · ${whenIn(t.askedAt, routineOf(t.routineId)?.timezone ?? "UTC")}` : ""}
                    {routineOf(t.routineId) && (
                      <Link className="ghost ghost--accent small" to={`/routines/${encodeURIComponent(t.routineId)}`}>
                        All runs
                      </Link>
                    )}
                  </p>
                )}
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
                    pinnable
                    onChoose={(q) => ask(q)}
                    onRoutine={t.question ? () => setRoutineSeed({ question: t.question, sources: t.sources }) : undefined}
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
          <Composer busy={busy} onAsk={ask} onRoutine={(question, sources) => setRoutineSeed({ question, sources })} />
          {fresh && <Suggestions onAsk={(q) => ask(q)} />}
        </div>
        <p className="footnote ask__promise">Cortex never shows what you couldn’t open yourself. Every question is logged.</p>
      </div>

      {routineSeed && <RoutineForm persona={persona.id} seed={routineSeed} onClose={() => setRoutineSeed(null)} />}
      {trust && <TrustPanel answer={shown} recordedAt={chosen?.recorded ? chosen.askedAt : undefined} highlightWhy={whyLit} onClose={() => openTrust(false)} />}
    </div>
  );
}

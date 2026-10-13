import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, CalendarClock, Check, CloudOff, Copy, ExternalLink, Pin, PinOff, ShieldCheck, ShieldQuestion, Sparkles, WifiOff } from "lucide-react";
import { useId, useState } from "react";
import { api } from "../api/client";
import type { AskResponse, Citation } from "../api/types";
import type { PersonaId } from "../auth/personas";
import { SourceGlyph, SOURCE_LABEL } from "../components/SourceGlyph";
import { SourceLink, useSourceViewer } from "../source/SourceViewer";
import { useFocus } from "../home/focus";
import { answerSegments, banners, shortId, uncitedClaims, when } from "./format";

// The excerpt popover's width and its gap from the card's edge, in px (.cite-tip in ask.css).
const TIP_WIDTH = 340;
const TIP_GAP = 12;

/**
 * One answer. Refusals use the same card, sections and footer as any other answer, and nothing in it
 * depends on why the answer was refused: a forbidden and a nonexistent document must look identical.
 * `asker` is whose answer this is: "why can I see this?" is asked as them (split-screen has several).
 */
export function AnswerCard({
  asker,
  answer,
  onWhyMore,
  onTrust,
  onChoose,
  onRoutine,
  pinnable = false,
}: {
  asker: PersonaId;
  answer: AskResponse;
  onWhyMore?: () => void; // absent where there is no Trust panel (split-screen)
  onTrust?: () => void; // phones: the "Trust" pill opens this answer's Trust panel as a bottom sheet
  onChoose: (question: string) => void;
  onRoutine?: () => void; // "Make this a routine": absent where there is no question to save (split-screen)
  pinnable?: boolean; // each source can be pinned to the asker's Current focus on My Work; off in split-screen
}) {
  // `active` lights a marker and its source row together. The excerpt opens from a marker only, and floats
  // over the card at that marker, so nothing below it moves.
  const viewer = useSourceViewer();
  const [active, setActive] = useState<number | null>(null);
  const [tipAt, setTipAt] = useState<{ n: number; left: number; top: number } | null>(null);
  const tipId = useId();
  const [copied, setCopied] = useState(false);
  const focus = useFocus(asker);
  const segments = answerSegments(answer);
  const tip = tipAt ? answer.citations[tipAt.n - 1] : null;
  const openTip = (n: number, marker: HTMLElement) => {
    setActive(n);
    const room = (marker.offsetParent?.clientWidth ?? 0) - TIP_WIDTH - TIP_GAP;
    setTipAt({ n, left: Math.max(TIP_GAP, Math.min(marker.offsetLeft, room)), top: marker.offsetTop + marker.offsetHeight + 6 });
  };
  const closeTip = () => {
    setActive(null);
    setTipAt(null);
  };
  const uncited = import.meta.env.DEV ? uncitedClaims(answer) : [];

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(segments.map((s) => (s.kind === "text" ? s.text : s.kind === "cite" ? `[${s.n}]` : " (lower confidence)")).join(""));
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // clipboard blocked
    }
  };

  return (
    <article className="card answer" aria-label="Answer" onMouseLeave={closeTip}>
      {banners(answer).map((b) => (
        <div key={b.kind} role="status" className={`banner banner--${b.kind}`}>
          {b.kind === "error" ? (
            <WifiOff size={16} aria-hidden />
          ) : b.kind === "unavailable" ? (
            <CloudOff size={16} aria-hidden />
          ) : (
            <AlertTriangle size={16} aria-hidden />
          )}
          <span>{b.text}</span>
        </div>
      ))}

      <div className="answer__byline">
        <Sparkles size={14} color="var(--orange)" aria-hidden />
        <strong>Cortex</strong>
        {answer.skill && <span className="muted">· {answer.skill}</span>}
        {onTrust && (
          <button type="button" className="chip answer__trust-pill" onClick={onTrust}>
            <ShieldCheck size={13} aria-hidden /> Trust
          </button>
        )}
      </div>

      <p className="answer__text">
        {segments.map((s, i) =>
          s.kind === "text" ? (
            <span key={i}>{s.text}</span>
          ) : s.kind === "lower" ? (
            <span key={i} className="chip chip--lower" title="A source supports this statement only weakly. Open the citation to check it.">
              lower confidence
            </span>
          ) : (
            <button
              key={i}
              type="button"
              className={`cite${active === s.n ? " cite--on" : ""}`}
              aria-label={`Source ${s.n}: ${s.citation.title}`}
              aria-describedby={tipAt?.n === s.n ? tipId : undefined}
              onMouseEnter={(e) => openTip(s.n, e.currentTarget)}
              onMouseLeave={closeTip}
              onFocus={(e) => openTip(s.n, e.currentTarget)}
              onBlur={closeTip}
              onClick={() => (viewer ? viewer.open({ docId: s.citation.doc_id, title: s.citation.title, asker }) : window.open(s.citation.url, "_blank", "noopener,noreferrer"))}
            >
              {s.n}
            </button>
          ),
        )}
      </p>

      {answer.refused && (
        <p className="answer__hint muted small">
          If you think it should exist, ask its owner or your manager.
        </p>
      )}

      {uncited.length > 0 && (
        <p className="banner banner--error small" role="status">
          Dev check: {uncited.length} statement(s) have no citation. The checker should have removed them.
        </p>
      )}

      {tip && tipAt && (
        <div id={tipId} role="tooltip" className="cite-tip" style={{ left: tipAt.left, top: tipAt.top }}>
          <div className="cite-tip__head">
            <SourceGlyph source={tip.source} size={14} /> {SOURCE_LABEL[tip.source]} · {tip.title}
          </div>
          <div className="eyebrow">Source excerpt</div>
          <blockquote className="excerpt">{tip.excerpt ?? "No excerpt available."}</blockquote>
        </div>
      )}

      {answer.clarify && (
        <div className="clarify">
          <p>{answer.clarify.question}</p>
          <div className="clarify__options">
            {answer.clarify.options.map((o) => (
              <button key={o} type="button" className="chip" onClick={() => onChoose(o)}>
                {o}
              </button>
            ))}
          </div>
        </div>
      )}

      <section className="answer__sources" aria-label="Sources">
        <div className="answer__sources-head">
          <span className="eyebrow">Sources</span>
          <span className="mono muted">{answer.citations.length}</span>
        </div>
        {answer.citations.length === 0 ? (
          <p className="muted small">No sources to show for this answer.</p>
        ) : (
          <ol className="sources">
            {answer.citations.map((c, i) => (
              <SourceRow
                asker={asker}
                key={c.doc_id}
                n={i + 1}
                citation={c}
                lit={active === i + 1}
                onHover={(on) => setActive(on ? i + 1 : null)}
                pinned={pinnable ? focus.ids.includes(c.doc_id) : undefined}
                onPin={() => (focus.ids.includes(c.doc_id) ? focus.remove(c.doc_id) : focus.put(c.doc_id))}
              />
            ))}
          </ol>
        )}
      </section>

      <footer className="answer__actions">
        <button type="button" className="ghost" onClick={copy}>
          {copied ? <Check size={14} aria-hidden /> : <Copy size={14} aria-hidden />} {copied ? "Copied" : "Copy"}
        </button>
        {onRoutine && (
          <button type="button" className="ghost" onClick={onRoutine}>
            <CalendarClock size={14} aria-hidden /> Make this a routine
          </button>
        )}
        {onWhyMore && (
          <button type="button" className="ghost ghost--accent" onClick={onWhyMore} aria-controls="trust-why">
            <ShieldQuestion size={14} aria-hidden /> Why might I not see everything?
          </button>
        )}
      </footer>
    </article>
  );
}

function SourceRow({
  asker,
  n,
  citation: c,
  lit,
  onHover,
  pinned,
  onPin,
}: {
  asker: PersonaId;
  n: number;
  citation: Citation;
  lit: boolean;
  onHover: (on: boolean) => void;
  pinned?: boolean; // undefined: no pin here
  onPin: () => void;
}) {
  const [why, setWhy] = useState(false);
  const proof = useQuery({
    queryKey: ["explain", asker, c.doc_id],
    queryFn: () => api(asker).explainAccess(c.doc_id),
    enabled: why,
  });

  return (
    <li className={`source${lit ? " source--lit" : ""}`} onMouseEnter={() => onHover(true)} onMouseLeave={() => onHover(false)}>
      <span className="source__n mono">{n}</span>
      <SourceGlyph source={c.source} size={16} />
      <span className="source__main">
        <span className="source__where mono">
          {SOURCE_LABEL[c.source]} · {shortId(c.doc_id)}
        </span>
        <SourceLink docId={c.doc_id} title={c.title} asker={asker} href={c.url} className="source__title">
          {c.title} <ExternalLink size={12} aria-hidden />
        </SourceLink>
      </span>
      {pinned !== undefined && (
        <button
          type="button"
          className="home__pin source__pin"
          aria-pressed={pinned}
          aria-label={`${pinned ? "Remove" : "Add"} ${c.title} ${pinned ? "from" : "to"} Current focus`}
          title={pinned ? "Remove from Current focus on My work" : "Pin to Current focus on My work"}
          onClick={onPin}
        >
          {pinned ? <PinOff size={14} aria-hidden /> : <Pin size={14} aria-hidden />}
        </button>
      )}
      <span className="source__meta">
        <span className="source__asof mono muted">as of {when(c.as_of)}</span>
        <button type="button" className="ghost small" aria-expanded={why} onClick={() => setWhy(!why)}>
          Why can I see this?
        </button>
      </span>
      {why && (
        <div className="source__why small">
          {proof.isPending && <span className="muted">Checking…</span>}
          {proof.data?.found && (
            <>
              You can see this because of:{" "}
              {proof.data.proof_path.map((p) => (
                <code key={p} className="mono pathstep">
                  {p}
                </code>
              ))}
            </>
          )}
          {proof.data && !proof.data.found && <span className="muted">You no longer have access to this source.</span>}
          {proof.isError && <span className="muted">Couldn't check right now.</span>}
        </div>
      )}
    </li>
  );
}

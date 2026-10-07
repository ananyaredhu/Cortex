import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Check, Copy, ExternalLink, ShieldQuestion, Sparkles, WifiOff } from "lucide-react";
import { useId, useState } from "react";
import { api } from "../api/client";
import type { AskResponse, Citation } from "../api/types";
import type { PersonaId } from "../auth/personas";
import { SourceGlyph, SOURCE_LABEL } from "../components/SourceGlyph";
import { answerSegments, banners, shortId, uncitedClaims, when } from "./format";

/**
 * One answer. Refusals use the same card, sections and footer as any other answer, and nothing in it
 * depends on why the answer was refused: a forbidden and a nonexistent document must look identical.
 * `asker` is whose answer this is: "why can I see this?" is asked as them (split-screen has several).
 */
export function AnswerCard({
  asker,
  answer,
  onWhyMore,
  onChoose,
}: {
  asker: PersonaId;
  answer: AskResponse;
  onWhyMore?: () => void; // absent where there is no Trust panel (split-screen)
  onChoose: (question: string) => void;
}) {
  const [active, setActive] = useState<number | null>(null);
  const tipId = useId();
  const [copied, setCopied] = useState(false);
  const segments = answerSegments(answer);
  const tip = active !== null ? answer.citations[active - 1] : null;
  const uncited = import.meta.env.DEV ? uncitedClaims(answer) : [];

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(segments.map((s) => (s.kind === "text" ? s.text : `[${s.n}]`)).join(""));
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // clipboard blocked
    }
  };

  return (
    <article className="card answer" aria-label="Answer" onMouseLeave={() => setActive(null)}>
      {banners(answer).map((b) => (
        <div key={b.kind} role="status" className={`banner banner--${b.kind}`}>
          {b.kind === "error" ? <WifiOff size={16} aria-hidden /> : <AlertTriangle size={16} aria-hidden />}
          <span>{b.text}</span>
        </div>
      ))}

      <div className="answer__byline">
        <Sparkles size={14} color="var(--orange)" aria-hidden />
        <strong>Cortex</strong>
        {answer.skill && <span className="muted">· {answer.skill}</span>}
      </div>

      <p className="answer__text">
        {segments.map((s, i) =>
          s.kind === "text" ? (
            <span key={i}>{s.text}</span>
          ) : (
            <button
              key={i}
              type="button"
              className={`cite${active === s.n ? " cite--on" : ""}`}
              aria-label={`Source ${s.n}: ${s.citation.title}`}
              aria-describedby={active === s.n ? tipId : undefined}
              onMouseEnter={() => setActive(s.n)}
              onFocus={() => setActive(s.n)}
              onBlur={() => setActive(null)}
              onClick={() => window.open(s.citation.url, "_blank", "noopener,noreferrer")}
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

      {tip && (
        <div id={tipId} role="tooltip" className="cite-tip">
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
              />
            ))}
          </ol>
        )}
      </section>

      <footer className="answer__actions">
        <button type="button" className="ghost" onClick={copy}>
          {copied ? <Check size={14} aria-hidden /> : <Copy size={14} aria-hidden />} {copied ? "Copied" : "Copy"}
        </button>
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
}: {
  asker: PersonaId;
  n: number;
  citation: Citation;
  lit: boolean;
  onHover: (on: boolean) => void;
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
        <a href={c.url} target="_blank" rel="noreferrer" className="source__title">
          {c.title} <ExternalLink size={12} aria-hidden />
        </a>
      </span>
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

import { Check, Copy, X } from "lucide-react";
import { useState } from "react";
import { SOURCES, type AskResponse, type FreshnessStatus } from "../api/types";
import { SourceGlyph, SOURCE_LABEL } from "../components/SourceGlyph";
import { when } from "./format";

const FRESH: Record<FreshnessStatus, { label: string; tone: string }> = {
  ok: { label: "Up to date", tone: "ok" },
  stale: { label: "Behind", tone: "warn" },
  unavailable: { label: "Unreachable", tone: "bad" },
};

/**
 * Per-answer trust signals. Section 1 counts only what the asker was shown: the contract forbids
 * candidate or denied counts (api.md 0.2, `coverage`; ui/MOCKUP-DRIFT.md #7).
 */
export function TrustPanel({
  answer,
  highlightWhy,
  onClose,
}: {
  answer: AskResponse | null;
  highlightWhy: boolean;
  onClose: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // clipboard blocked: the id is still selectable
    }
  };

  return (
    <aside className="trust" aria-label="Trust panel">
      <header className="trust__head">
        <span className="trust__title">Trust</span>
        <span className="muted trust__for">{answer ? "for the selected answer" : "no answer yet"}</span>
        <button className="icon-button" onClick={onClose} aria-label="Close trust panel">
          <X size={16} />
        </button>
      </header>

      {!answer ? (
        <p className="trust__empty muted">Ask a question to see where its answer came from.</p>
      ) : (
        <div className="trust__body">
          <section>
            <h2 className="eyebrow">1 · Sources searched</h2>
            <table className="trust__table">
              <thead>
                <tr>
                  <th scope="col">Source</th>
                  <th scope="col">Searched</th>
                  <th scope="col">Cited</th>
                </tr>
              </thead>
              <tbody>
                {SOURCES.map((s) => {
                  const c = answer.coverage?.[s];
                  return (
                    <tr key={s}>
                      <th scope="row">
                        <SourceGlyph source={s} size={14} /> {SOURCE_LABEL[s]}
                      </th>
                      <td>{c ? (c.searched ? "Yes" : "No") : "—"}</td>
                      <td className="num">{c ? c.shown : "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <p className={`trust__note${highlightWhy ? " trust__note--lit" : ""}`} id="trust-why">
              Cortex only reads what you can already open yourself. Anything else is never counted, named or
              shown, so this panel can't tell you whether more exists. If you think something is missing, ask its
              owner or your manager.
            </p>
          </section>

          <section>
            <h2 className="eyebrow">2 · Freshness</h2>
            <ul className="trust__fresh">
              {SOURCES.map((s) => {
                const f = answer.freshness?.per_source?.[s];
                const look = f ? FRESH[f.status] : null;
                return (
                  <li key={s}>
                    <SourceGlyph source={s} size={14} />
                    <span className="trust__fresh-name">{SOURCE_LABEL[s]}</span>
                    <span className="muted mono">{f ? when(f.last_sync) : "—"}</span>
                    {look && (
                      <span className={`status status--${look.tone}`}>
                        <span className="status__dot" aria-hidden />
                        {look.label}
                      </span>
                    )}
                  </li>
                );
              })}
            </ul>
            <p className="muted small">Target: changes searchable within 5 minutes, never more than 1 hour.</p>
          </section>

          <section>
            <h2 className="eyebrow">3 · Grounding</h2>
            {answer.grounding ? (
              <>
                <div className="trust__ground">
                  <span className="trust__score">{Math.round(answer.grounding.score * 100)}%</span>
                  <span className="muted small">Share of statements checked against a source you can open.</span>
                </div>
                <div className="meter" role="img" aria-label={`${Math.round(answer.grounding.score * 100)} percent grounded`}>
                  <div className="meter__fill" style={{ width: `${answer.grounding.score * 100}%` }} />
                </div>
                {answer.grounding.removed_claims > 0 && (
                  <p className="small">
                    {answer.grounding.removed_claims} unsupported statement
                    {answer.grounding.removed_claims > 1 ? "s were" : " was"} removed.
                  </p>
                )}
              </>
            ) : (
              <p className="muted small">No statements to check.</p>
            )}
          </section>

          <section className="trust__ids">
            <div>
              <h2 className="eyebrow">4 · Policy</h2>
              <span className="mono">{answer.policy_version ?? "—"}</span>
            </div>
            <div>
              <h2 className="eyebrow">5 · Audit reference</h2>
              <span className="mono">{answer.request_id}</span>
              <button className="icon-button" onClick={() => copy(answer.request_id)} aria-label="Copy audit reference">
                {copied ? <Check size={14} /> : <Copy size={14} />}
              </button>
              <p className="small status status--ok">
                <Check size={12} aria-hidden /> Recorded in the audit log.
              </p>
            </div>
          </section>
        </div>
      )}
    </aside>
  );
}

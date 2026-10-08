import { useQuery } from "@tanstack/react-query";
import { ArrowRight, BellRing, ExternalLink, Hash, SquareKanban } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { shortId, when } from "../ask/format";
import { usePersona } from "../auth/PersonaContext";
import { SourceGlyph } from "../components/SourceGlyph";
import "./home.css";

/**
 * My Work: only what /v1/mywork returns, all of it through the PDP (ui/MOCKUP-DRIFT.md #24).
 * The mockup's sprint board, team and timeline need data the API doesn't have, so they are left out.
 */
export function HomePage() {
  const { persona } = usePersona();
  const navigate = useNavigate();
  const work = useQuery({ queryKey: ["mywork", persona.id], queryFn: () => api(persona.id).mywork() });
  const ask = (question: string) => navigate("/ask", { state: { ask: question } });
  const w = work.data;

  return (
    <div className="page home">
      <p className="eyebrow">My work</p>
      <h1 className="display">
        Hello, {w?.user.display_name ?? persona.name}.
        {w && (
          <>
            <br />
            <strong>
              {w.alerts.length
                ? `${w.alerts.length} answer${w.alerts.length > 1 ? "s" : ""} changed since you asked.`
                : "Here’s what you’re working on."}
            </strong>
          </>
        )}
      </h1>
      {work.isError && <p className="banner banner--error">{(work.error as Error).message}</p>}

      {w && w.alerts.length > 0 && (
        <section className="card home__alerts" aria-labelledby="h-needs">
          <h2 id="h-needs" className="h3">
            <BellRing size={16} aria-hidden /> Needs you
          </h2>
          <ul>
            {w.alerts.map((a) => (
              <li key={`${a.request_id}-${a.changed_doc}`}>
                <div>
                  <strong>{a.changed_title ?? shortId(a.changed_doc)}</strong> changed on {when(a.changed_at)}, after you
                  asked{a.question ? <> “{a.question}”</> : null}.
                  <span className="muted small"> Your earlier answer may be out of date.</span>
                </div>
                {a.question && (
                  <button type="button" className="ghost ghost--accent" onClick={() => ask(a.question!)}>
                    Ask again <ArrowRight size={14} aria-hidden />
                  </button>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {w && (
        <div className="home__grid">
          <section className="card home__panel" aria-labelledby="h-tickets">
            <h2 id="h-tickets" className="h3">
              <SquareKanban size={16} aria-hidden /> My tickets
            </h2>
            {w.issues.length === 0 ? (
              <p className="muted small">No tickets you can see.</p>
            ) : (
              <ul className="home__list">
                {w.issues.map((i) => (
                  <li key={i.doc_id}>
                    <a href={i.url} target="_blank" rel="noreferrer">
                      {i.title} <ExternalLink size={12} aria-hidden />
                    </a>
                    {i.status && <span className="home__status">{i.status}</span>}
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="card home__panel" aria-labelledby="h-pages">
            <h2 id="h-pages" className="h3">Recent pages</h2>
            {w.recent_pages.length === 0 ? (
              <p className="muted small">No recent pages.</p>
            ) : (
              <ul className="home__list">
                {w.recent_pages.map((p) => (
                  <li key={p.doc_id}>
                    <SourceGlyph source={p.doc_id.startsWith("gdrive:") ? "gdrive" : "confluence"} size={14} />
                    <a href={p.url} target="_blank" rel="noreferrer">
                      {p.title}
                    </a>
                    <span className="muted small mono">{when(p.updated_at)}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="card home__panel" aria-labelledby="h-where">
            <h2 id="h-where" className="h3">Projects and channels</h2>
            <div className="home__chips">
              {w.projects.map((p) => (
                <span key={p} className="chip chip--static mono">
                  <SquareKanban size={12} aria-hidden /> {shortId(p)}
                </span>
              ))}
              {w.channels.map((c) => (
                <span key={c} className="chip chip--static mono">
                  <Hash size={12} aria-hidden /> {shortId(c)}
                </span>
              ))}
              {w.projects.length + w.channels.length === 0 && <p className="muted small">None yet.</p>}
            </div>
          </section>

          <section className="card home__panel" aria-labelledby="h-ask">
            <h2 id="h-ask" className="h3">Ask Cortex about your work</h2>
            <ul className="home__asks">
              {w.suggested_questions.map((q) => (
                <li key={q}>
                  <button type="button" onClick={() => ask(q)}>
                    <span>{q}</span>
                    <ArrowRight size={14} aria-hidden />
                  </button>
                </li>
              ))}
            </ul>
          </section>
        </div>
      )}
    </div>
  );
}

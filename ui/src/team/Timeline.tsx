import type { Team } from "../api/types";
import { at, shortDay, span } from "./dates";

/**
 * One row per project that has a start and a target date. The dates and the milestone are kept by hand in the project
 * registry; the fill is the share of tickets done among those this person can open.
 */
export function Timeline({ teams, today }: { teams: Team[]; today: string }) {
  const rows = teams.filter((t) => t.start_date && t.target_date);
  if (rows.length === 0) return null;
  const s = span(
    rows.flatMap((t) => [t.start_date!, t.target_date!, ...(t.milestone ? [t.milestone.date] : [])]),
    today,
  );
  const now = at(s, today) + 100 / (s.weeks.length * 7) / 2; // the middle of today
  const showNow = now >= 0 && now <= 100;

  return (
    <section className="card team__timeline" aria-labelledby="h-timeline">
      <h2 id="h-timeline" className="h3">
        Timeline <span className="muted small team__timeline-note">by week · dates are kept by hand in the project registry</span>
      </h2>
      <div className="team__timeline-scroll">
        <div className="team__timeline-grid">
          <span />
          <div className="team__weeks" style={{ gridTemplateColumns: `repeat(${s.weeks.length}, minmax(0, 1fr))` }} aria-hidden>
            {s.weeks.map((w, i) => (
              <span key={i} className={w.current ? "team__week team__week--now" : "team__week"}>
                {w.label}
              </span>
            ))}
          </div>
          {rows.map((t) => {
            const left = at(s, t.start_date!);
            const width = at(s, t.target_date!, true) - left;
            const { done, total } = t.issues;
            const summary =
              `${t.name}: ${shortDay(t.start_date!)} to ${shortDay(t.target_date!)}` +
              (total > 0 ? `, ${done} of ${total} tickets done` : "") +
              (t.milestone ? `, ${t.milestone.label} on ${shortDay(t.milestone.date)}` : "");
            return (
              <div key={t.project_id} className="team__timeline-row" role="img" aria-label={summary}>
                <div className="team__timeline-name">
                  <strong>{t.name}</strong>
                  {t.leads.length > 0 && <span className="muted small">{t.leads.join(", ")}</span>}
                </div>
                <div className="team__track">
                  {showNow && <span className="team__now" style={{ left: `${now}%` }} />}
                  <span className="team__span" style={{ left: `${left}%`, width: `${width}%` }}>
                    {total > 0 && <span style={{ width: `${(done / total) * 100}%` }} />}
                  </span>
                  {t.milestone && (
                    <>
                      <span className="team__milestone" style={{ left: `${at(s, t.milestone.date)}%` }} />
                      <span className="team__milestone-label mono" style={{ left: `${at(s, t.milestone.date)}%` }}>
                        {t.milestone.label}
                      </span>
                    </>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}

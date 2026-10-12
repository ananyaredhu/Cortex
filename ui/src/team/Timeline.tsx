import { useId, useState } from "react";
import type { Team } from "../api/types";
import { at, daysUntil, shortDay, span, sprintDay } from "./dates";

// The hover card's width and its distance from the pointer, in px (.team__tip in team.css).
const TIP_WIDTH = 300;
const TIP_GAP = 14;

/**
 * One row per project that has a start and a target date. The dates and the milestone are kept by hand in the project
 * registry; the fill is the share of tickets done among those this person can open. Pointing at a row, or tabbing to
 * its bar, shows the project's details in a card.
 */
export function Timeline({ teams, today }: { teams: Team[]; today: string }) {
  const [tip, setTip] = useState<{ id: string; x: number; y: number } | null>(null);
  const tipId = useId();
  const rows = teams.filter((t) => t.start_date && t.target_date);
  if (rows.length === 0) return null;
  const s = span(
    rows.flatMap((t) => [t.start_date!, t.target_date!, ...(t.milestone ? [t.milestone.date] : [])]),
    today,
  );
  const now = at(s, today) + 100 / (s.weeks.length * 7) / 2; // the middle of today
  const showNow = now >= 0 && now <= 100;
  const shown = tip && rows.find((t) => t.project_id === tip.id);

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
              <div
                key={t.project_id}
                className="team__timeline-row"
                onMouseMove={(e) => setTip({ id: t.project_id, x: e.clientX, y: e.clientY })}
                onMouseLeave={() => setTip(null)}
              >
                <div className="team__timeline-name">
                  <strong>{t.name}</strong>
                  {t.leads.length > 0 && <span className="muted small">{t.leads.join(", ")}</span>}
                </div>
                <div
                  className="team__track"
                  role="img"
                  aria-label={summary}
                  aria-describedby={tip?.id === t.project_id ? tipId : undefined}
                  tabIndex={0}
                  onFocus={(e) => {
                    const box = e.currentTarget.getBoundingClientRect();
                    setTip({ id: t.project_id, x: box.left, y: box.bottom - TIP_GAP });
                  }}
                  onBlur={() => setTip(null)}
                >
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
      <ul className="team__legend" aria-label="How to read the timeline">
        <li>
          <i className="team__key-span" /> Start to target date
        </li>
        <li>
          <i className="team__key-done" /> Tickets done, of those you can open
        </li>
        <li>
          <i className="team__key-now" /> Today
        </li>
        <li>
          <i className="team__key-milestone" /> Milestone
        </li>
      </ul>
      {tip && shown && <Details id={tipId} team={shown} today={today} x={tip.x} y={tip.y} />}
    </section>
  );
}

/** What the page knows about one project, all of it already in /v1/team for this person. */
function Details({ id, team: t, today, x, y }: { id: string; team: Team; today: string; x: number; y: number }) {
  const left = Math.max(8, Math.min(x + TIP_GAP, window.innerWidth - TIP_WIDTH - 8));
  // Under the pointer while there is room for it, else above.
  const below = y < window.innerHeight * 0.6;
  const days = daysUntil(today, t.target_date!);
  const sprint = t.sprint && sprintDay(t.sprint, today);
  return (
    <div id={id} role="tooltip" className="team__tip" style={below ? { left, top: y + TIP_GAP } : { left, bottom: window.innerHeight - y + TIP_GAP }}>
      <strong>{t.name}</strong>
      {t.goal && <p>{t.goal}</p>}
      <dl>
        {t.leads.length > 0 && (
          <>
            <dt>Lead</dt>
            <dd>{t.leads.join(", ")}</dd>
          </>
        )}
        <dt>Dates</dt>
        <dd>
          {shortDay(t.start_date!)} – {shortDay(t.target_date!)}
        </dd>
        <dt>Target</dt>
        <dd>{days > 0 ? `${days} day${days > 1 ? "s" : ""} left` : days === 0 ? "today" : `${-days} day${days < -1 ? "s" : ""} ago`}</dd>
        {t.milestone && (
          <>
            <dt>Milestone</dt>
            <dd>
              {t.milestone.label} · {shortDay(t.milestone.date)}
            </dd>
          </>
        )}
        {t.issues.total > 0 && (
          <>
            <dt>Tickets</dt>
            <dd>
              {t.issues.done} of {t.issues.total} done
            </dd>
          </>
        )}
        {t.sprint && sprint && (
          <>
            <dt>Sprint</dt>
            <dd>
              {t.sprint.name}
              {sprint.state === "during" ? ` · day ${sprint.day} of ${sprint.of}` : sprint.state === "before" ? ` · starts ${shortDay(t.sprint.start_date)}` : " · ended"}
              {t.sprint.total > 0 ? ` · ${t.sprint.done}/${t.sprint.total} done` : ""}
            </dd>
          </>
        )}
      </dl>
      {t.issues.total > 0 && <span className="muted small">Counted from the tickets you can open.</span>}
    </div>
  );
}

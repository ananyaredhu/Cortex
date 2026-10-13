import { useQuery } from "@tanstack/react-query";
import { ArrowRight } from "lucide-react";
import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { Source, Team } from "../api/types";
import { shortId, when } from "../ask/format";
import { usePersona } from "../auth/PersonaContext";
import type { PersonaId } from "../auth/personas";
import { SourceGlyph } from "../components/SourceGlyph";
import "../home/home.css";
import { Board } from "./Board";
import { isoDate, shortDay as day, sprintDay } from "./dates";
import "./team.css";
import { Timeline } from "./Timeline";

/**
 * Team focus: the projects the person is a member of, from /v1/team.
 * Every count is over documents this person may open, so two members of one project can see different numbers.
 * The timeline's dates and the sprint's name, dates and goal are kept by hand in the project registry; Jira's own sprint
 * field is not ingested, so the sprint's numbers follow a rule the API states (docs/02-contracts/api.md).
 */

/** `today` is for tests; the page uses the viewer's own date. */
export function TeamPage({ today }: { today?: string }) {
  const { persona } = usePersona();
  const navigate = useNavigate();
  const now = useMemo(() => today ?? isoDate(new Date()), [today]);
  const team = useQuery({ queryKey: ["team", persona.id], queryFn: () => api(persona.id).team() });
  const teams = team.data?.teams;

  return (
    <div className="page home team">
      <p className="eyebrow">Team focus</p>
      <h1 className="display">
        What your teams are working on.
        {teams && teams.length > 0 && (
          <>
            <br />
            <strong>
              {teams.length} project{teams.length > 1 ? "s" : ""}, from what you can open.
            </strong>
          </>
        )}
      </h1>
      {team.isError && <p className="banner banner--error">{(team.error as Error).message}</p>}
      {teams && teams.length === 0 && <p className="muted">You are not part of a project yet.</p>}

      {teams && <Timeline teams={teams} today={now} />}

      {teams?.map((t) => (
        <TeamCard
          key={t.project_id}
          team={t}
          persona={persona.id}
          today={now}
          onAsk={() => navigate("/ask", { state: { ask: `What is the status of the ${t.name.toLowerCase()}, and what is blocking it?` } })}
        />
      ))}
    </div>
  );
}

function SprintStrip({ sprint, name, today }: { sprint: NonNullable<Team["sprint"]>; name: string; today: string }) {
  const d = sprintDay(sprint, today);
  const where = d.state === "before" ? `starts ${day(sprint.start_date)}` : d.state === "after" ? "ended" : `day ${d.day} of ${d.of}`;
  return (
    <div className="team__sprint">
      <div className="team__sprint-what">
        <span className="mono small muted">
          {sprint.name} · {day(sprint.start_date)} – {day(sprint.end_date)} · {where}
        </span>
        {sprint.goal && <strong>{sprint.goal}</strong>}
      </div>
      {sprint.total > 0 && (
        <div className="team__progress">
          <div className="team__count">
            <strong>
              {sprint.done}/{sprint.total}
            </strong>{" "}
            sprint tickets done
          </div>
          <div
            className="team__bar"
            role="progressbar"
            aria-label={`${name}: sprint tickets done`}
            aria-valuemin={0}
            aria-valuemax={sprint.total}
            aria-valuenow={sprint.done}
          >
            <span style={{ width: `${(sprint.done / sprint.total) * 100}%` }} />
            {d.of > 0 && <i style={{ left: `${(d.day / d.of) * 100}%` }} title="Time gone in the sprint" />}
          </div>
          <p className="team__bar-key">Filled: tickets done, of those you can open. Orange mark: time gone in the sprint.</p>
        </div>
      )}
    </div>
  );
}

function TeamCard({ team: t, persona, today, onAsk }: { team: Team; persona: PersonaId; today: string; onAsk: () => void }) {
  const { total, done, by_status } = t.issues;
  const id = `team-${t.project_id}`;
  return (
    <section className="card team__card" aria-labelledby={id}>
      <header className="team__head">
        <div>
          <h2 id={id} className="team__name">
            {t.name}
          </h2>
          <p className="muted small team__meta">
            {t.leads.length > 0 && <span>Lead {t.leads.join(", ")}</span>}
            {t.target_date && <span>Target {day(t.target_date)}</span>}
          </p>
        </div>
        <button type="button" className="ghost ghost--accent" onClick={onAsk}>
          Ask for the status <ArrowRight size={14} aria-hidden />
        </button>
      </header>
      {t.goal && <p className="team__goal">{t.goal}</p>}

      {t.sprint && <SprintStrip sprint={t.sprint} name={t.name} today={today} />}

      {total > 0 && (
        <div className="team__count">
          <strong>
            {done} of {total}
          </strong>{" "}
          tickets done in the project
          <span className="team__statuses">
            {by_status.map((s) => (
              <span key={s.status} className="home__status">
                {s.status} {s.count}
              </span>
            ))}
          </span>
        </div>
      )}

      <div className="home__chips">
        {t.places.map((p) => (
          <span key={p} className="chip chip--static mono">
            <PlaceGlyph place={p} /> {shortId(p)}
          </span>
        ))}
      </div>

      <Board team={t} persona={persona} />

      <div className="team__cols">
        <div>
          <h3 className="h3">Recent updates</h3>
          {t.updates.length === 0 ? (
            <p className="muted small">No recent updates.</p>
          ) : (
            <ul className="home__list">
              {t.updates.map((u) => (
                <li key={u.doc_id}>
                  <SourceGlyph source={u.source} size={14} />
                  <a href={u.url} target="_blank" rel="noreferrer">
                    {u.title}
                  </a>
                  <span className="muted small team__when">
                    {u.author ? `${u.author} · ` : ""}
                    <span className="mono">{when(u.updated_at)}</span>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </section>
  );
}

function PlaceGlyph({ place }: { place: string }) {
  return <SourceGlyph source={place.slice(0, place.indexOf(":")) as Source} size={12} />;
}

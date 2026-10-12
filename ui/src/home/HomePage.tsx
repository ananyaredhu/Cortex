import { useQuery } from "@tanstack/react-query";
import { ArrowRight, CalendarClock, Repeat, ChevronDown, ChevronUp, Circle, CircleCheck, CircleDashed, CircleDot, Crosshair, ExternalLink, Flag, Pin, PinOff, X } from "lucide-react";
import { type DragEvent, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { MyWork } from "../api/types";
import { keyAndTitle, shortId, when } from "../ask/format";
import { usePersona } from "../auth/PersonaContext";
import { PERSONAS } from "../auth/personas";
import { Avatar } from "../components/Avatar";
import { SOURCE_LABEL, SourceGlyph } from "../components/SourceGlyph";
import { FilterBar } from "./FilterBar";
import { outcomeText, scheduleText, whenIn } from "../routines/format";
import "../routines/routines.css";
import { shortDay } from "../team/dates";
import { dueState, filterOptions, type Filters, matches } from "./filters";
import { useFocus, type WorkItem, workItems } from "./focus";
import "./home.css";

// How many rows a list shows before "Show all": /v1/mywork returns every ticket the person can open (ui/MOCKUP-DRIFT.md #50).
const PREVIEW = 5;
// What a dragged row carries: the document's id and nothing else.
const DRAG = "application/x-cortex-doc";

const placeLabel = (w: MyWork, place: string) => w.channel_names?.[place] ?? shortId(place);

// A ticket's status as a shape, not a colour: green, amber and red are kept for trust state (ui/MOCKUP-DRIFT.md #45).
function StatusMark({ status }: { status?: string | null }) {
  const Icon = !status ? CircleDashed : /^(done|closed|resolved)$/i.test(status) ? CircleCheck : /progress|review/i.test(status) ? CircleDot : Circle;
  return (
    <span className="home__status">
      <Icon size={12} aria-hidden /> {status ?? "No status"}
    </span>
  );
}

/**
 * My Work: only what /v1/mywork and /v1/team return, all of it through the PDP (ui/MOCKUP-DRIFT.md #24).
 * The team's side is on Team focus (/team); the mockup's sprint board and timeline need data the API doesn't have.
 */
export function HomePage() {
  const { persona } = usePersona();
  const work = useQuery({ queryKey: ["mywork", persona.id], queryFn: () => api(persona.id).mywork() });
  // Same query as Team focus: it knows whose each ticket is, its status, and the newest threads. My Work still renders without it.
  const team = useQuery({ queryKey: ["team", persona.id], queryFn: () => api(persona.id).team() });
  const focus = useFocus(persona.id);
  const [filters, setFilters] = useState<Filters>({});
  const [allTickets, setAllTickets] = useState(false);
  const [over, setOver] = useState(false);
  const w = work.data;

  const all = useMemo(() => (w ? workItems(w, team.data?.teams) : { tickets: [], updates: [], places: [] }), [w, team.data]);
  const options = useMemo(() => filterOptions([...all.tickets, ...all.updates], all.places, (p) => (w ? placeLabel(w, p) : p)), [all, w]);
  const filtered = Object.values(filters).some((v) => v.length > 0);
  const shown = (items: WorkItem[]) => items.filter((i) => matches(i, filters));
  const tickets = shown(all.tickets);
  const updates = shown(all.updates);
  // A pinned id is drawn only while the API still returns the document; anything else stays out of sight.
  const known = new Map([...all.tickets, ...all.updates].map((i) => [i.doc_id, i]));
  const pinned = shown(focus.ids.flatMap((id) => known.get(id) ?? []));

  const drag = (id: string) => (e: DragEvent) => {
    e.dataTransfer.setData(DRAG, id);
    e.dataTransfer.effectAllowed = "copyMove";
  };
  const accepts = (e: DragEvent) => e.dataTransfer.types.includes(DRAG);
  const drop = (before?: string) => (e: DragEvent) => {
    if (!accepts(e)) return;
    e.preventDefault();
    e.stopPropagation();
    setOver(false);
    focus.put(e.dataTransfer.getData(DRAG), before);
  };

  const pin = (i: WorkItem) => {
    const isPinned = focus.ids.includes(i.doc_id);
    return (
      <button
        type="button"
        className="home__pin"
        aria-pressed={isPinned}
        aria-label={`${isPinned ? "Remove" : "Add"} ${i.title} ${isPinned ? "from" : "to"} Current focus`}
        title={isPinned ? "Remove from Current focus" : "Add to Current focus"}
        onClick={() => (isPinned ? focus.remove(i.doc_id) : focus.put(i.doc_id))}
      >
        {isPinned ? <PinOff size={14} aria-hidden /> : <Pin size={14} aria-hidden />}
      </button>
    );
  };

  const row = (i: WorkItem) => (
    <li key={i.doc_id} draggable onDragStart={drag(i.doc_id)}>
      <SourceGlyph source={i.source} size={14} />
      <a href={i.url} target="_blank" rel="noreferrer" draggable={false}>
        {i.title} <ExternalLink size={12} aria-hidden />
      </a>
      <span className="muted small mono">{when(i.updated_at)}</span>
      {pin(i)}
    </li>
  );

  // A ticket: its title, then what the API knows about it. The deadline and the priority are there when the Jira
  // connector carries them (api.md 0.3). "Overdue" is said in words, not in red: red is kept for trust state (#45).
  const ticket = (i: WorkItem) => {
    const { key, title } = keyAndTitle(i);
    const due = dueState(i);
    return (
      <li key={i.doc_id} className="home__ticket" draggable onDragStart={drag(i.doc_id)}>
        <div className="home__ticket-main">
          <a href={i.url} target="_blank" rel="noreferrer" draggable={false}>
            {title} <ExternalLink size={12} aria-hidden />
          </a>
          <div className="home__ticket-meta">
            <span className="mono">{key}</span>
            <StatusMark status={i.status} />
            {i.assignee && (
              <span className="home__who">
                <Avatar src={PERSONAS.find((p) => p.name === i.assignee)?.avatar} name={i.assignee} size={18} /> {i.assignee}
              </span>
            )}
            {i.due_date && (
              <span className={`home__due${due === "overdue" ? " home__due--overdue" : ""}`}>
                <CalendarClock size={12} aria-hidden /> {due === "overdue" ? "Overdue · due" : "Due"} {shortDay(i.due_date)}
              </span>
            )}
            {i.priority && (
              <span className="home__who">
                <Flag size={12} aria-hidden /> {i.priority}
              </span>
            )}
            {i.mine && <span className="home__mine">Yours</span>}
          </div>
        </div>
        {pin(i)}
      </li>
    );
  };

  return (
    <div className="page home">
      <p className="eyebrow">My work</p>
      <h1 className="display">
        Hello, {w?.user.display_name ?? persona.name}.
        {w && (
          <>
            <br />
            <strong>Here’s what you’re working on.</strong>
          </>
        )}
      </h1>
      {work.isError && <p className="banner banner--error">{(work.error as Error).message}</p>}

      {w && <FilterBar value={filters} options={options} onChange={setFilters} />}

      {w && (
        <section
          className={`card home__focus${over ? " home__focus--over" : ""}`}
          aria-labelledby="h-focus"
          onDragOver={(e) => {
            if (!accepts(e)) return;
            e.preventDefault();
            setOver(true);
          }}
          onDragLeave={(e) => {
            if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setOver(false);
          }}
          onDrop={drop()}
        >
          <div className="home__head">
            <h2 id="h-focus" className="h3">
              <Crosshair size={16} aria-hidden /> Current focus
              {pinned.length > 0 && <span className="muted mono small">{pinned.length}</span>}
            </h2>
            <span className="muted small">Drag a ticket, page or thread here, or use its pin. Drag cards to reorder.</span>
          </div>
          {pinned.length === 0 ? (
            <p className="home__focus-empty muted small">
              {filtered && focus.ids.some((id) => known.has(id)) ? "Nothing in your focus matches these filters." : "Nothing here yet. Drop what matters most right now."}
            </p>
          ) : (
            <ul className="home__focus-cards">
              {pinned.map((i) => (
                <li key={i.doc_id} draggable onDragStart={drag(i.doc_id)} onDrop={drop(i.doc_id)}>
                  <SourceGlyph source={i.source} size={18} />
                  <div className="home__focus-body">
                    <a href={i.url} target="_blank" rel="noreferrer" draggable={false}>
                      {i.title}
                    </a>
                    <span className="muted small">
                      {SOURCE_LABEL[i.source]}
                      {i.place ? ` · ${placeLabel(w, i.place)}` : ""}
                      {i.status ? ` · ${i.status}` : ""}
                    </span>
                  </div>
                  <button type="button" className="home__pin" aria-label={`Remove ${i.title} from Current focus`} onClick={() => focus.remove(i.doc_id)}>
                    <X size={14} aria-hidden />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {w?.routines && w.routines.some((r) => r.status === "active") && (
        <section className="card home__panel" aria-labelledby="h-routines">
          <div className="home__head">
            <h2 id="h-routines" className="h3">
              <Repeat size={16} aria-hidden /> Routines
            </h2>
            <Link className="ghost ghost--accent small" to="/routines">
              All routines <ArrowRight size={12} aria-hidden />
            </Link>
          </div>
          <ul className="home__list home__routines">
            {w.routines
              .filter((r) => r.status === "active")
              .map((r) => {
                const run = r.last_run;
                return (
                  <li key={r.routine_id}>
                    {run?.unread ? <span className="routines__dot" role="img" aria-label="Unread" /> : <CalendarClock size={14} aria-hidden />}
                    <span className="home__routine">
                      {run?.conversation_id ? (
                        <Link to={`/ask/${encodeURIComponent(run.conversation_id)}`}>{r.name}</Link>
                      ) : (
                        <Link to="/routines">{r.name}</Link>
                      )}
                      <span className="muted small">
                        {run ? `${outcomeText(run)} · ${whenIn(run.ran_at, r.timezone)}` : `${scheduleText(r.schedule)} · first run ${whenIn(r.next_run_at, r.timezone)}`}
                      </span>
                    </span>
                  </li>
                );
              })}
          </ul>
        </section>
      )}

      {w && (
        <>
          <section className="card home__panel" aria-labelledby="h-tickets">
            <div className="home__head">
              <h2 id="h-tickets" className="h3">
                <SourceGlyph source="jira" size={16} /> My tickets
                {tickets.length > 0 && <span className="muted mono small">{tickets.length}</span>}
              </h2>
              <Link className="ghost ghost--accent small" to="/team">
                Team board <ArrowRight size={12} aria-hidden />
              </Link>
            </div>
            {tickets.length === 0 ? (
              <p className="muted small">{filtered ? "No tickets match these filters." : "No tickets you can see."}</p>
            ) : (
              <>
                <ul id="home-tickets" className="home__list">
                  {(allTickets ? tickets : tickets.slice(0, PREVIEW)).map(ticket)}
                </ul>
                {tickets.length > PREVIEW && (
                  <button
                    type="button"
                    className="ghost small home__more"
                    aria-expanded={allTickets}
                    aria-controls="home-tickets"
                    onClick={() => setAllTickets(!allTickets)}
                  >
                    {allTickets ? "Show fewer" : `Show all ${tickets.length}`}
                    {allTickets ? <ChevronUp size={12} aria-hidden /> : <ChevronDown size={12} aria-hidden />}
                  </button>
                )}
              </>
            )}
          </section>

          <section className="card home__panel" aria-labelledby="h-recent">
            <h2 id="h-recent" className="h3">Recent pages and threads</h2>
            {updates.length === 0 ? (
              <p className="muted small">{filtered ? "Nothing recent matches these filters." : "Nothing recent."}</p>
            ) : (
              <ul className="home__list">{updates.slice(0, filtered ? undefined : PREVIEW).map(row)}</ul>
            )}
          </section>
        </>
      )}
    </div>
  );
}

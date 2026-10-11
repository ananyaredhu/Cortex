import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import type { AuditAnswer, AuditEvent, AuditFilter, SeenDoc, TimeTravel, VerifyResult } from "../api/types";
import { usePersona } from "../auth/PersonaContext";
import { PERSONAS } from "../auth/personas";
import { SourceGlyph, SOURCE_LABEL } from "../components/SourceGlyph";
import { when } from "../ask/format";
import { ChainWidget } from "./ChainWidget";
import { EventDrawer } from "./EventDrawer";
import { counts, EVENT_LABEL, personaLabel, sourcesHit } from "./format";
import "./audit.css";

// Scenario 5 (docs/04-scenarios.md), prefilled so the demo starts one click away.
const SCENARIO_5 = "Show me everything Priya accessed in the PAY Confluence space in the last 30 days";
const SPACES = [
  { value: "", label: "Any space" },
  { value: "confluence:PAY", label: "Confluence › PAY" },
  { value: "confluence:ENG", label: "Confluence › ENG" },
  { value: "confluence:SEC", label: "Confluence › SEC" },
  { value: "confluence:HR", label: "Confluence › HR" },
];

export function AuditPage() {
  const { persona } = usePersona();
  const [question, setQuestion] = useState(SCENARIO_5);
  const [filter, setFilter] = useState<AuditFilter>({ user: "priya@companya.com", space: "confluence:PAY", decision: "all" });
  // Two ways in. "Ask" sends the question in words to the audit agent (POST /v1/audit/ask), which plans it, says how it
  // understood it and runs it. "Run query" sends the filters below as they are (POST /v1/audit/query).
  const [submitted, setSubmitted] = useState<{ question: string; filter: AuditFilter } | null>(null);
  const [asked, setAsked] = useState<{ question: string; n: number } | null>(null);
  const [mode, setMode] = useState<"ask" | "filter">("filter");
  const [selected, setSelected] = useState<AuditEvent | null>(null);

  const qc = useQueryClient();
  const results = useQuery({
    queryKey: ["audit", persona.id, submitted],
    // Every audit query is itself logged, so refresh the integrity widget's entry count afterwards.
    queryFn: () =>
      api(persona.id)
        .auditQuery({ question: submitted!.question, filter: clean(submitted!.filter) })
        .finally(() => qc.invalidateQueries({ queryKey: ["verify", persona.id] })),
    enabled: submitted !== null,
  });
  const answer = useQuery({
    queryKey: ["audit-ask", persona.id, asked],
    queryFn: () =>
      api(persona.id)
        .auditAsk(asked!.question)
        .finally(() => qc.invalidateQueries({ queryKey: ["verify", persona.id] })),
    enabled: asked !== null,
  });
  const verify = useQuery({ queryKey: ["verify", persona.id], queryFn: () => api(persona.id).auditVerify() });
  const brokenSeq = verify.data && !verify.data.ok ? verify.data.first_broken_seq : null;
  const asking = mode === "ask";
  const active = asking ? answer : results;
  const idle = asking ? asked === null : submitted === null;
  const plan = asking ? (answer.data?.plan ?? null) : null;
  const tabular = !asking || plan?.kind === "query";
  const events = (asking ? eventsOf(answer.data) : results.data?.events) ?? [];
  const firstAsk = events.find((e) => e.event_type === "ask")?.seq ?? events[0]?.seq ?? null;

  const run = () => {
    setSelected(null);
    setMode("filter");
    setSubmitted({ question, filter: { ...filter } });
  };
  const ask = () => {
    if (!question.trim()) return;
    setSelected(null);
    setMode("ask");
    setAsked((prev) => ({ question: question.trim(), n: (prev?.n ?? 0) + 1 })); // n: asking the same words again runs again
  };

  return (
    <div className={`audit${selected ? " audit--drawer" : ""}`}>
      <div className="audit__main">
        <header className="audit__head">
          <h1 className="h2">Audit console</h1>
          <ChainWidget officer={persona.id} tamperSeq={firstAsk} />
        </header>

        <form
          className="audit__query"
          onSubmit={(e) => {
            e.preventDefault();
            run();
          }}
        >
          <div className="audit__ask">
            <Search size={16} aria-hidden />
            <label htmlFor="audit-q" className="visually-hidden">
              Audit question
            </label>
            <input
              id="audit-q"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault(); // Enter in the question box asks the question; the filters have their own button
                  ask();
                }
              }}
            />
            <button type="button" className="audit__run" onClick={ask}>
              Ask
            </button>
            <button type="submit" className="audit__run audit__run--quiet">
              Run query
            </button>
          </div>
          <div className="audit__filters">
            <label>
              <span>User</span>
              <select value={filter.user ?? ""} onChange={(e) => setFilter({ ...filter, user: e.target.value || undefined })}>
                <option value="">Anyone</option>
                {PERSONAS.map((p) => (
                  <option key={p.id} value={p.email}>
                    {p.name} · {p.email}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>Space</span>
              <select value={filter.space ?? ""} onChange={(e) => setFilter({ ...filter, space: e.target.value || undefined })}>
                {SPACES.map((s) => (
                  <option key={s.value} value={s.value}>
                    {s.label}
                  </option>
                ))}
              </select>
            </label>
            <fieldset className="audit__decision">
              <legend>Decision</legend>
              {(["all", "allowed", "denied"] as const).map((d) => (
                <button
                  key={d}
                  type="button"
                  className="chip"
                  aria-pressed={(filter.decision ?? "all") === d}
                  onClick={() => setFilter({ ...filter, decision: d })}
                >
                  {d === "all" ? "All" : d === "allowed" ? "Allowed" : "Denied"}
                </button>
              ))}
            </fieldset>
          </div>
          <p className="muted small">
            A space matches documents that were allowed: denied ones are stored as salted hashes, so they can’t be
            tied to a space. <strong>Ask</strong> reads the question in words and shows how it was understood;{" "}
            <strong>Run query</strong> uses the filters above as they are. Either way the query is recorded in the audit
            log too.
          </p>
        </form>

        <section className="audit__results" aria-label="Results">
          {idle ? (
            <p className="muted">Ask a question, or run a query, to reconstruct what people asked and what they were shown.</p>
          ) : active.isPending ? (
            <p className="muted">Searching the log…</p>
          ) : active.isError ? (
            <p role="alert" className="banner banner--error">
              {(active.error as Error).message}
            </p>
          ) : asking && !plan ? (
            <p role="status" className="audit__understood">
              {answer.data?.clarify ?? "That question could not be read. Try naming a person, a space or a date."}
            </p>
          ) : (
            <>
              {asking && plan && (
                <div className="audit__understood" role="status">
                  <p>
                    <span className="muted">Understood as:</span> {plan.understood}
                  </p>
                  {answer.data?.summary && <p className="audit__summary">{answer.data.summary}</p>}
                </div>
              )}
              {asking && plan?.kind === "time_travel" && <TimeTravelView result={answer.data!.result as TimeTravel} />}
              {asking && plan?.kind === "verify" && <VerifyView result={answer.data!.result as VerifyResult} />}
            </>
          )}
          {idle || active.isPending || active.isError || !tabular || (asking && !plan) ? null : events.length === 0 ? (
            <p className="muted">No entries match. Ask a few questions as that person first, then run the query again.</p>
          ) : (
            <>
              <p className="small muted">
                {events.length} {events.length === 1 ? "entry" : "entries"}
              </p>
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">Time (UTC)</th>
                    <th scope="col" className="num">Entry</th>
                    <th scope="col">Type</th>
                    <th scope="col">User</th>
                    <th scope="col">Question</th>
                    <th scope="col">Sources</th>
                    <th scope="col" className="num">Allowed</th>
                    <th scope="col" className="num">Denied</th>
                    <th scope="col">Chain</th>
                  </tr>
                </thead>
                <tbody>
                  {events.map((e) => {
                    const c = counts(e);
                    const broken = brokenSeq !== null && e.seq >= brokenSeq;
                    return (
                      <tr
                        key={e.seq}
                        tabIndex={0}
                        aria-selected={selected?.seq === e.seq}
                        className={selected?.seq === e.seq ? "is-selected" : undefined}
                        onClick={() => setSelected(e)}
                        onKeyDown={(k) => {
                          if (k.key === "Enter" || k.key === " ") {
                            k.preventDefault();
                            setSelected(e);
                          }
                        }}
                      >
                        <td className="mono small">{when(e.ts)}</td>
                        <td className="num mono">{e.seq}</td>
                        <td className="small">{EVENT_LABEL[e.event_type] ?? e.event_type}</td>
                        <td className="small">{personaLabel(e.actor.user_id).split(" (")[0]}</td>
                        <td className="audit__qcell" title={e.query?.text}>
                          {e.query?.text}
                        </td>
                        <td>
                          {sourcesHit(e).map((s) => (
                            <SourceGlyph key={s} source={s} size={14} />
                          ))}
                          <span className="visually-hidden">{sourcesHit(e).map((s) => SOURCE_LABEL[s]).join(", ")}</span>
                        </td>
                        <td className="num">{c.allowed}</td>
                        <td className="num">{c.denied}</td>
                        <td>
                          <span className={`status status--${broken ? "bad" : "ok"}`}>
                            <span className="status__dot" aria-hidden />
                            {broken ? (e.seq === brokenSeq ? "Broken" : "Untrusted") : "OK"}
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </>
          )}
        </section>
      </div>

      {selected && (
        <EventDrawer
          key={selected.seq}
          officer={persona.id}
          event={events.find((e) => e.seq === selected.seq) ?? selected}
          brokenSeq={brokenSeq}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
}

function eventsOf(answer: AuditAnswer | undefined): AuditEvent[] | undefined {
  const result = answer?.result;
  return result && "events" in result ? result.events : undefined;
}

function DocList({ title, docs, empty }: { title: string; docs: SeenDoc[]; empty: string }) {
  return (
    <section className="audit__seen" aria-label={title}>
      <h2 className="audit__seen-title">
        {title} <span className="muted">({docs.length})</span>
      </h2>
      {docs.length === 0 ? (
        <p className="muted small">{empty}</p>
      ) : (
        <ul>
          {docs.map((d) =>
            "restricted" in d ? (
              <li key={d.doc_id} className="muted">
                <span className="mono small">{d.doc_id}</span> · restricted: you can’t open this one, so its title is withheld
              </li>
            ) : (
              <li key={d.doc_id}>
                <SourceGlyph source={d.source} size={14} /> {d.title} <span className="mono small muted">{d.doc_id}</span>
              </li>
            ),
          )}
        </ul>
      )}
    </section>
  );
}

function TimeTravelView({ result }: { result: TimeTravel }) {
  if (!result.identity.known) {
    const first = result.identity.first_recorded_at;
    return (
      <p className="muted">
        Nothing is recorded about what {result.user} could open at {when(result.at)}: it is before the first time their access
        was recorded{first ? " (" + when(first) + ")" : ""}. No guess is shown.
      </p>
    );
  }
  return (
    <div className="audit__travel">
      <DocList title={"Could open at " + when(result.at)} docs={result.could_see} empty="Nothing." />
      <DocList title="Lost since" docs={result.changed_since.lost} empty="Nothing lost since then." />
      <DocList title="Gained since" docs={result.changed_since.gained} empty="Nothing gained since then." />
    </div>
  );
}

function VerifyView({ result }: { result: VerifyResult }) {
  return result.ok ? (
    <p className="status status--ok">
      <span className="status__dot" aria-hidden /> Log integrity verified: {result.checked} entries, {result.checkpoints} signed
      checkpoints.
    </p>
  ) : (
    <p role="alert" className="banner banner--error">
      The log does not verify from entry {result.first_broken_seq}: {result.reason}
    </p>
  );
}

function clean(f: AuditFilter): AuditFilter {
  return Object.fromEntries(Object.entries(f).filter(([, v]) => v !== undefined && v !== "")) as AuditFilter;
}

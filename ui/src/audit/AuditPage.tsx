import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import type { AuditEvent, AuditFilter } from "../api/types";
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
  const [submitted, setSubmitted] = useState<{ question: string; filter: AuditFilter } | null>(null);
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
  const verify = useQuery({ queryKey: ["verify", persona.id], queryFn: () => api(persona.id).auditVerify() });
  const brokenSeq = verify.data && !verify.data.ok ? verify.data.first_broken_seq : null;
  const events = results.data?.events ?? [];
  const firstAsk = events.find((e) => e.event_type === "ask")?.seq ?? events[0]?.seq ?? null;

  const run = () => {
    setSelected(null);
    setSubmitted({ question, filter: { ...filter } });
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
            <input id="audit-q" value={question} onChange={(e) => setQuestion(e.target.value)} />
            <button type="submit" className="audit__run">
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
            tied to a space. This query is recorded in the audit log too.
          </p>
        </form>

        <section className="audit__results" aria-label="Results">
          {submitted === null ? (
            <p className="muted">Run a query to reconstruct what people asked and what they were shown.</p>
          ) : results.isPending ? (
            <p className="muted">Searching the log…</p>
          ) : results.isError ? (
            <p role="alert" className="banner banner--error">
              {(results.error as Error).message}
            </p>
          ) : events.length === 0 ? (
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

function clean(f: AuditFilter): AuditFilter {
  return Object.fromEntries(Object.entries(f).filter(([, v]) => v !== undefined && v !== "")) as AuditFilter;
}

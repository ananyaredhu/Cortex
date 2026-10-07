import { useMutation, useQuery } from "@tanstack/react-query";
import { AlertTriangle, Check, CircleCheck, CircleX, ExternalLink, Minus, X } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import type { Source } from "../api/types";
import { when } from "../ask/format";
import { usePersona } from "../auth/PersonaContext";
import { PERSONAS } from "../auth/personas";
import { SourceGlyph, SOURCE_LABEL } from "../components/SourceGlyph";
import { duration, health, type Health } from "./format";
import { LagChart } from "./LagChart";
import "./admin.css";

const TONE_ICON = { ok: Check, warn: AlertTriangle, bad: X, none: Minus } as const;

export function AdminPage() {
  const { persona } = usePersona();
  const freshness = useQuery({ queryKey: ["freshness", persona.id], queryFn: () => api(persona.id).freshness() });
  const leakci = useQuery({ queryKey: ["leakci", persona.id], queryFn: () => api(persona.id).leakci() });
  const policies = useQuery({ queryKey: ["policies", persona.id], queryFn: () => api(persona.id).policyVersions() });
  const sources = Object.entries(freshness.data?.sources ?? {});

  return (
    <div className="admin">
      <header>
        <h1 className="h2">Policy &amp; connectors</h1>
        <p className="muted small">
          {sources.length} connectors
          {policies.data && <> · active policy <span className="mono">{policies.data.active}</span></>}
          {freshness.data && <> · as of {when(freshness.data.as_of)}</>}
        </p>
      </header>

      <section aria-labelledby="h-connectors">
        <h2 id="h-connectors" className="eyebrow">Connector health</h2>
        {freshness.isError && <p className="banner banner--error">{(freshness.error as Error).message}</p>}
        <div className="admin__cards">
          {sources.map(([name, s]) => {
            const h = health(s);
            return (
              <article key={name} className="card admin__card" aria-label={`${SOURCE_LABEL[name as Source] ?? name} connector`}>
                <header>
                  <SourceGlyph source={name as Source} size={16} />
                  <strong>{SOURCE_LABEL[name as Source] ?? name}</strong>
                  <HealthChip h={h} />
                </header>
                <dl className="kv">
                  <dt>Last success</dt>
                  <dd className="mono small">{when(s.last_ok_at)}</dd>
                  <dt>Freshness p50 / p95</dt>
                  <dd>
                    {s.freshness_lag_seconds.count
                      ? `${duration(s.freshness_lag_seconds.p50)} / ${duration(s.freshness_lag_seconds.p95)}`
                      : "—"}
                  </dd>
                  <dt>Pipeline p95</dt>
                  <dd>{s.pipeline_lag_seconds.count ? duration(s.pipeline_lag_seconds.p95) : "—"}</dd>
                  <dt>Changes</dt>
                  <dd className="num-left">{s.freshness_lag_seconds.count}</dd>
                </dl>
                {s.last_error && <p className="banner banner--error small">{s.last_error}</p>}
              </article>
            );
          })}
        </div>
      </section>

      <div className="admin__split">
        <section aria-labelledby="h-sla" className="card admin__panel">
          <h2 id="h-sla" className="eyebrow">Freshness SLA</h2>
          {freshness.data && <LagChart report={freshness.data} />}
        </section>

        <section aria-labelledby="h-traps" className="card admin__panel">
          <h2 id="h-traps" className="eyebrow">Trap tests (Leak-CI)</h2>
          {leakci.data && (
            <>
              <p className="admin__hero">
                <span className={`admin__big ${leakci.data.leaks ? "is-bad" : ""}`}>{leakci.data.leaks}</span>
                <span>
                  leaks in {leakci.data.cases} cases
                  <br />
                  <span className="muted small">last run {when(leakci.data.as_of)}</span>
                </span>
              </p>
              <ul className="admin__suites">
                {(leakci.data.suites ?? []).map((t) => (
                  <li key={t.name}>
                    {t.failed ? (
                      <CircleX size={16} className="status--bad" aria-hidden />
                    ) : (
                      <CircleCheck size={16} className="status--ok" aria-hidden />
                    )}
                    <span>{t.name}</span>
                    <span className="muted small">{t.category}</span>
                    <span className={`small ${t.failed ? "status--bad" : "status--ok"}`}>
                      {t.failed ? `${t.failed} failed` : `${t.passed} passed`}
                    </span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
      </div>

      <div className="admin__split">
        <section aria-labelledby="h-policy" className="card admin__panel">
          <h2 id="h-policy" className="eyebrow">Policy versions</h2>
          <p className="muted small">Policy is plain, tested code in brain/policy/. Changes ship only through reviewed PRs.</p>
          <ul className="admin__versions">
            {policies.data?.versions.map((v) => (
              <li key={v.policy_version}>
                <span className="mono">{v.policy_version}</span>
                {v.policy_version === policies.data.active && <span className="chip chip--static">active</span>}
                <span className="muted small">
                  {v.author} · {when(v.created_at)}
                </span>
                {v.pr_url && (
                  <a href={v.pr_url} target="_blank" rel="noreferrer" className="small">
                    PR <ExternalLink size={12} aria-hidden />
                  </a>
                )}
              </li>
            ))}
          </ul>
        </section>
        <EvaluateSandbox />
      </div>
    </div>
  );
}

function HealthChip({ h }: { h: Health }) {
  const Icon = TONE_ICON[h.tone];
  return (
    <span className={`status status--${h.tone === "none" ? "none" : h.tone}`}>
      <Icon size={13} aria-hidden /> {h.label}
    </span>
  );
}

function EvaluateSandbox() {
  const { persona } = usePersona();
  const [user, setUser] = useState(PERSONAS[1].email);
  const [docId, setDocId] = useState("");
  const evaluate = useMutation({ mutationFn: () => api(persona.id).policyEvaluate(user, docId.trim()) });

  return (
    <section aria-labelledby="h-eval" className="card admin__panel">
      <h2 id="h-eval" className="eyebrow">Evaluate sandbox</h2>
      <p className="muted small">Would this person be allowed to see this document? Admins only; every check is logged.</p>
      <form
        className="admin__eval"
        onSubmit={(e) => {
          e.preventDefault();
          if (docId.trim()) evaluate.mutate();
        }}
      >
        <label>
          <span>User</span>
          <select value={user} onChange={(e) => setUser(e.target.value)}>
            {PERSONAS.map((p) => (
              <option key={p.id} value={p.email}>
                {p.name} · {p.email}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Document id</span>
          <input className="mono" value={docId} placeholder="e.g. confluence:SEC/q3-breach-report" onChange={(e) => setDocId(e.target.value)} />
        </label>
        <button type="submit" className="audit__run" disabled={!docId.trim() || evaluate.isPending}>
          Evaluate
        </button>
      </form>
      {evaluate.isError && <p className="banner banner--error small">{(evaluate.error as Error).message}</p>}
      {evaluate.data && (
        <div className={`admin__verdict ${evaluate.data.allowed ? "is-allow" : "is-deny"}`} role="status">
          <strong>
            {evaluate.data.allowed ? <CircleCheck size={16} aria-hidden /> : <CircleX size={16} aria-hidden />}{" "}
            {evaluate.data.allowed ? "Allowed" : "Denied"}
          </strong>
          <span className="small">
            Rule <span className="mono">{evaluate.data.rule}</span> · policy <span className="mono">{evaluate.data.policy_version}</span>
          </span>
          {evaluate.data.proof_path.length > 0 && (
            <span className="small">
              Because of:{" "}
              {evaluate.data.proof_path.map((p) => (
                <code key={p} className="mono pathstep">
                  {p}
                </code>
              ))}
            </span>
          )}
        </div>
      )}
    </section>
  );
}

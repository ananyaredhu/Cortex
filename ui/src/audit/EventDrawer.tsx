import { useQuery } from "@tanstack/react-query";
import { Check, History, Lock, ShieldAlert, X } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { AuditEvent, ReplayCitation } from "../api/types";
import type { PersonaId } from "../auth/personas";
import { SourceGlyph, SOURCE_LABEL } from "../components/SourceGlyph";
import { when } from "../ask/format";
import { EVENT_LABEL, personaLabel, shortHash, sourceOf } from "./format";

/**
 * One audit entry. Shows ids and hashes; titles and answer text only where the viewing officer may see the
 * documents themselves (the API withholds the rest). Denied documents appear only as salted hashes.
 */
export function EventDrawer({
  officer,
  event: e,
  brokenSeq,
  onClose,
}: {
  officer: PersonaId;
  event: AuditEvent;
  brokenSeq: number | null;
  onClose: () => void;
}) {
  const [replayOpen, setReplayOpen] = useState(false);
  const replay = useQuery({
    queryKey: ["replay", officer, e.request_id],
    queryFn: () => api(officer).auditReplay(e.request_id),
    enabled: e.event_type === "ask",
  });
  useEffect(() => {
    if (!replayOpen) return;
    const close = (k: KeyboardEvent) => k.key === "Escape" && setReplayOpen(false);
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [replayOpen]);
  const titles = new Map(
    (replay.data?.then.citations ?? []).filter((c): c is Extract<ReplayCitation, { title: string }> => "title" in c).map((c) => [c.doc_id, c.title]),
  );

  return (
    <aside className="drawer" aria-label={`Audit entry ${e.seq}`}>
      <header className="drawer__head">
        <div>
          <div className="mono muted small">
            {e.request_id} · entry {e.seq} · {when(e.ts)}
          </div>
          <strong>{EVENT_LABEL[e.event_type] ?? e.event_type}</strong>
        </div>
        <button type="button" className="icon-button" onClick={onClose} aria-label="Close entry">
          <X size={16} />
        </button>
      </header>

      <div className="drawer__body">
        {brokenSeq !== null && e.seq >= brokenSeq && (
          <div className="banner banner--error" role="status">
            <ShieldAlert size={16} aria-hidden />
            <span>
              {e.seq === brokenSeq
                ? "This entry does not match its hash: it was changed after it was written."
                : `This entry comes after the break at entry ${brokenSeq}, so it can't be trusted until the chain is re-anchored.`}
            </span>
          </div>
        )}

        <section>
          <p className="drawer__q">{e.query?.text ?? "—"}</p>
          <p className="muted small">
            {personaLabel(e.actor.user_id)} · {e.actor.roles.join(", ")} · from {e.actor.client}
          </p>
          {!!e.flags?.length && (
            <p className="small">
              Flags:{" "}
              {e.flags.map((f) => (
                <code key={f} className="mono pathstep">
                  {f}
                </code>
              ))}
            </p>
          )}
        </section>

        {e.answer && (
          <section>
            <h2 className="eyebrow">Answer</h2>
            <dl className="kv">
              <dt>Hash</dt>
              <dd className="mono">{shortHash(e.answer.sha256)}</dd>
              <dt>Refused</dt>
              <dd>{e.answer.refused ? "Yes (uniform refusal)" : "No"}</dd>
            </dl>
            {e.answer.text ? (
              <blockquote className="excerpt">{e.answer.text}</blockquote>
            ) : (
              <p className="muted small">
                <Lock size={12} aria-hidden /> Answer text withheld: it cites sources you can’t open yourself. The hash
                above still proves what was sent.
              </p>
            )}
            <button type="button" className="ghost" onClick={() => setReplayOpen(true)} disabled={!replay.data}>
              <History size={14} aria-hidden /> Replay answer
            </button>
          </section>
        )}

        {e.decisions.length > 0 && (
          <section>
            <h2 className="eyebrow">Per-document decisions</h2>
            <table className="table table--dense">
              <thead>
                <tr>
                  <th scope="col">Document</th>
                  <th scope="col">Decision</th>
                  <th scope="col">Policy · ACL</th>
                  <th scope="col">Live</th>
                </tr>
              </thead>
              <tbody>
                {e.decisions.map((d, i) => {
                  const src = d.doc_id ? sourceOf(d.doc_id) : null;
                  return (
                    <tr key={i}>
                      <td>
                        {d.doc_id ? (
                          <>
                            <span className="doccell">
                              {src && <SourceGlyph source={src} size={13} />}
                              <span className="mono">{d.doc_id}</span>
                            </span>
                            <span className="small muted">{titles.get(d.doc_id) ?? (src ? `${SOURCE_LABEL[src]} · restricted item` : "")}</span>
                          </>
                        ) : (
                          <>
                            <span className="mono">{shortHash(d.doc_id_hash)}</span>
                            <span className="small muted">restricted item (salted hash)</span>
                          </>
                        )}
                      </td>
                      <td>
                        <span className={`status status--${d.allowed ? "ok" : "bad"}`}>
                          <span className="status__dot" aria-hidden />
                          {d.allowed ? "Allowed" : "Denied"}
                        </span>
                      </td>
                      <td className="mono small">
                        {d.policy_version} · {shortHash(d.acl_snapshot_hash)}
                      </td>
                      <td>{d.jit_checked ? <Check size={14} aria-label="Re-checked live" /> : <span className="muted">—</span>}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <p className="muted small">Titles appear only where you can open the document yourself.</p>
          </section>
        )}

        <section>
          <h2 className="eyebrow">Chain</h2>
          <dl className="kv mono small">
            <dt>prev_hash</dt>
            <dd>{shortHash(e.prev_hash)}</dd>
            <dt>entry_hash</dt>
            <dd>{shortHash(e.hash)}</dd>
          </dl>
        </section>
      </div>

      {replayOpen && replay.data && (
        <div className="replay" role="dialog" aria-modal="true" aria-label="Replay">
          <div className="replay__panel">
            <header className="drawer__head">
              <strong>Replay · {e.request_id}</strong>
              <button type="button" className="icon-button" onClick={() => setReplayOpen(false)} aria-label="Close replay">
                <X size={16} />
              </button>
            </header>
            <div className="replay__cols">
              <ReplayColumn title="What they were shown" policy={replay.data.then.policy_version} items={replay.data.then.citations} changes={replay.data.differences} />
              <ReplayColumn title="What they would see now" policy={replay.data.now.policy_version} items={replay.data.now.citations} changes={[]} />
            </div>
            <p className="muted small">
              {replay.data.differences.length
                ? `${replay.data.differences.length} difference(s). Rebuilt from the logged document versions and policy, not the live index.`
                : "No differences. Rebuilt from the logged document versions and policy, not the live index."}
            </p>
          </div>
        </div>
      )}
    </aside>
  );
}

const CHANGE_LABEL = { revoked: "Access removed since", edited: "Edited since", deleted: "Deleted since" };

function ReplayColumn({
  title,
  policy,
  items,
  changes,
}: {
  title: string;
  policy: string;
  items: ReplayCitation[];
  changes: { doc_id: string; change: "revoked" | "edited" | "deleted" }[];
}) {
  return (
    <section className="card replay__col">
      <h3 className="h3">{title}</h3>
      <p className="mono muted small">policy {policy}</p>
      {items.length === 0 ? (
        <p className="muted small">No sources.</p>
      ) : (
        <ul className="replay__list">
          {items.map((c) => {
            const change = changes.find((x) => x.doc_id === c.doc_id);
            return (
              <li key={c.doc_id} className={change ? "replay__changed" : undefined}>
                <span className="mono small">{c.doc_id}</span>
                <span>{"title" in c ? c.title : "Restricted item"}</span>
                {change && <span className="status status--warn small">{CHANGE_LABEL[change.change]}</span>}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

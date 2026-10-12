import { useMutation, useQueryClient } from "@tanstack/react-query";
import { SquareKanban } from "lucide-react";
import { type DragEvent, useState } from "react";
import { api, ApiError } from "../api/client";
import type { Team } from "../api/types";
import { keyAndTitle } from "../ask/format";
import type { PersonaId } from "../auth/personas";
import { PERSONAS } from "../auth/personas";
import { Avatar } from "../components/Avatar";

// The board's columns, in this order; a status outside them gets its own column after these, and an issue with none
// goes under "No status".
const COLUMNS = ["To Do", "In Progress", "In Review", "Done"];
const NO_STATUS = "No status";
// What a dragged card carries: the issue's id and nothing else.
const DRAG = "application/x-cortex-issue";

type Issue = Team["issues"]["items"][number];

function columns(items: Issue[]): { status: string; items: Issue[] }[] {
  const by = new Map<string, Issue[]>();
  for (const i of items) by.set(i.status ?? NO_STATUS, [...(by.get(i.status ?? NO_STATUS) ?? []), i]);
  const extra = [...by.keys()].filter((s) => !COLUMNS.includes(s) && s !== NO_STATUS).sort();
  const order = [...COLUMNS, ...extra, ...(by.has(NO_STATUS) ? [NO_STATUS] : [])];
  return order.map((status) => ({ status, items: by.get(status) ?? [] }));
}

// Why a move did not happen, in the reader's words. The server's own message is used where it has one to give.
function refusal(e: unknown): string {
  if (e instanceof ApiError && e.status === 404) return "That ticket isn’t available to you any more. Nothing was changed.";
  if (e instanceof ApiError && e.status === 409) return "Jira doesn’t allow that move from the ticket’s current status. Nothing was changed.";
  if (e instanceof ApiError && e.status === 403) return "Only the assignee can move a ticket. Nothing was changed.";
  return "Jira couldn’t be reached, so the ticket was not moved. Try again in a moment.";
}

/**
 * One project's board. A card the person may move (`can_move`: they are its assignee, by account) can be dragged to
 * another column, or moved with its menu from the keyboard. The move is made in Jira by POST /v1/issues/move, which
 * decides again whether it is allowed; the card moves at once and goes back if the answer is no (api.md 0.3).
 */
export function Board({ team: t, persona }: { team: Team; persona: PersonaId }) {
  const qc = useQueryClient();
  const [over, setOver] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const key = ["team", persona];

  const move = useMutation({
    mutationFn: ({ docId, status }: { docId: string; status: string }) => api(persona).moveIssue(docId, status),
    onMutate: async ({ docId, status }) => {
      setProblem(null);
      await qc.cancelQueries({ queryKey: key });
      const before = qc.getQueryData<{ teams: Team[] }>(key);
      qc.setQueryData<{ teams: Team[] }>(key, (data) =>
        data && {
          teams: data.teams.map((team) => ({
            ...team,
            issues: { ...team.issues, items: team.issues.items.map((i) => (i.doc_id === docId ? { ...i, status } : i)) },
          })),
        },
      );
      return { before };
    },
    onError: (e, _vars, context) => {
      if (context?.before) qc.setQueryData(key, context.before);
      setProblem(refusal(e));
    },
    // Whatever happened, show what Jira now holds: the counts, the sprint and My Work all follow from it.
    onSettled: () => {
      qc.invalidateQueries({ queryKey: key });
      qc.invalidateQueries({ queryKey: ["mywork", persona] });
    },
  });

  const request = (docId: string, status: string) => {
    const issue = t.issues.items.find((i) => i.doc_id === docId);
    if (!issue?.can_move || issue.status === status || move.isPending) return;
    move.mutate({ docId, status });
  };
  const accepts = (e: DragEvent, status: string) => status !== NO_STATUS && e.dataTransfer.types.includes(DRAG);
  const movable = t.issues.items.some((i) => i.can_move);

  return (
    <div>
      <h3 className="h3">
        <SquareKanban size={16} aria-hidden /> Board
        {movable && <span className="muted small team__board-note">Drag your own tickets to change their status. The change is made in Jira.</span>}
      </h3>
      {problem && (
        <p role="alert" className="banner banner--stale">
          {problem}
        </p>
      )}
      {t.issues.items.length === 0 ? (
        <p className="muted small">No tickets you can see.</p>
      ) : (
        <div className="team__board">
          {columns(t.issues.items).map((col) => (
            <section
              key={col.status}
              className={`team__col${over === col.status ? " team__col--over" : ""}`}
              aria-label={`${t.name}: ${col.status}`}
              onDragOver={(e) => {
                if (!accepts(e, col.status)) return;
                e.preventDefault();
                setOver(col.status);
              }}
              onDragLeave={(e) => {
                if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setOver(null);
              }}
              onDrop={(e) => {
                if (!accepts(e, col.status)) return;
                e.preventDefault();
                setOver(null);
                request(e.dataTransfer.getData(DRAG), col.status);
              }}
            >
              <h4 className="team__col-head">
                {col.status} <span className="muted mono">{col.items.length}</span>
              </h4>
              <ul>
                {col.items.map((i) => {
                  const { key: issueKey, title } = keyAndTitle(i);
                  return (
                    <li
                      key={i.doc_id}
                      className={`team__ticket${i.mine ? " team__ticket--mine" : ""}${i.can_move ? " team__ticket--movable" : ""}`}
                      draggable={Boolean(i.can_move)}
                      onDragStart={(e) => {
                        e.dataTransfer.setData(DRAG, i.doc_id);
                        e.dataTransfer.effectAllowed = "move";
                      }}
                    >
                      <a href={i.url} target="_blank" rel="noreferrer" draggable={false}>
                        {title}
                      </a>
                      <span className="team__ticket-foot">
                        <span className="mono">{issueKey}</span>
                        {i.mine && <span className="team__mine">Yours</span>}
                        {i.assignee && <Avatar src={PERSONAS.find((p) => p.name === i.assignee)?.avatar} name={i.assignee} size={22} />}
                      </span>
                      {i.can_move && i.status && (
                        <label className="team__move">
                          <span className="muted">Move to</span>
                          <select
                            aria-label={`Move ${issueKey} to`}
                            value={i.status}
                            disabled={move.isPending}
                            onChange={(e) => request(i.doc_id, e.target.value)}
                          >
                            {[...new Set([...COLUMNS, i.status])].map((s) => (
                              <option key={s} value={s}>
                                {s}
                              </option>
                            ))}
                          </select>
                        </label>
                      )}
                    </li>
                  );
                })}
              </ul>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}

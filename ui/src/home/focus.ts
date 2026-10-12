import { useCallback, useEffect, useState } from "react";
import type { MyWork, Source, Team } from "../api/types";

/** One row on My Work: a ticket, a page or a thread, as the API returned it for this person. */
export interface WorkItem {
  doc_id: string;
  title: string;
  url?: string;
  source: Source;
  place?: string;
  status?: string | null;
  assignee?: string | null;
  mine?: boolean;
  updated_at?: string | null;
  due_date?: string | null; // YYYY-MM-DD (api.md 0.3)
  priority?: string | null;
}

export const sourceOf = (id: string) => id.slice(0, id.indexOf(":")) as Source;

/** Whether a document lives in a place (`jira:DBMIG`, `slack:C_DBMIG`, `confluence:PAY`): by the place the API named, else by its id. */
export const inPlace = (item: { doc_id: string; place?: string }, place: string) =>
  item.place === place || item.doc_id.startsWith(`${place}-`) || item.doc_id.startsWith(`${place}/`);

// The person's own open tickets lead, then their finished ones, then everyone else's.
const rank = (i: WorkItem) => (i.mine ? (/^(done|closed|resolved)$/i.test(i.status ?? "") ? 1 : 0) : 2);

/** Everything My Work can show, from /v1/mywork and (when it has loaded) /v1/team. Both went through the PDP. */
export function workItems(w: MyWork, teams: Team[] = []) {
  const onTeam = new Map(teams.flatMap((t) => t.issues.items).map((i) => [i.doc_id, i]));
  const tickets: WorkItem[] = w.issues
    .map((i) => ({
      ...i,
      source: "jira" as const,
      place: w.projects.find((p) => inPlace(i, p)),
      // /mywork carries these itself from 0.3; before that, /v1/team is where they are
      status: i.status ?? onTeam.get(i.doc_id)?.status,
      assignee: i.assignee ?? onTeam.get(i.doc_id)?.assignee,
      mine: i.mine ?? onTeam.get(i.doc_id)?.mine ?? false,
    }))
    .sort((a, b) => rank(a) - rank(b));

  // Pages from /mywork plus the teams' newest threads and files; tickets already have their own list.
  const recent = new Map<string, WorkItem>();
  for (const u of teams.flatMap((t) => t.updates)) if (u.source !== "jira") recent.set(u.doc_id, u);
  for (const p of w.recent_pages) {
    const source = sourceOf(p.doc_id);
    const slash = p.doc_id.indexOf("/");
    const place = recent.get(p.doc_id)?.place ?? (source === "confluence" && slash > 0 ? p.doc_id.slice(0, slash) : undefined);
    recent.set(p.doc_id, { ...p, source, place });
  }
  const updates = [...recent.values()].sort((a, b) => (b.updated_at ?? "").localeCompare(a.updated_at ?? ""));

  const places = [...new Set([...w.projects, ...w.channels, ...updates.flatMap((u) => (u.place ? [u.place] : []))])];
  return { tickets, updates, places };
}

const key = (persona: string) => `cortex.focus.${persona}`;
const read = (persona: string): string[] => {
  try {
    const ids: unknown = JSON.parse(localStorage.getItem(key(persona)) ?? "[]");
    return Array.isArray(ids) ? ids.filter((i): i is string => typeof i === "string") : [];
  } catch {
    return [];
  }
};

/**
 * The person's Current focus: an ordered list of document ids, kept in this browser.
 * Only ids are stored, never titles or links: a card is drawn from what the API returns now, so a document
 * the person can no longer open simply is not there (ui/MOCKUP-DRIFT.md #51).
 */
export function useFocus(persona: string) {
  const [ids, setIds] = useState(() => read(persona));
  useEffect(() => setIds(read(persona)), [persona]);
  const save = useCallback(
    (next: (ids: string[]) => string[]) =>
      setIds((ids) => {
        const out = next(ids);
        try {
          localStorage.setItem(key(persona), JSON.stringify(out));
        } catch {
          // private window or storage full: the focus lasts for this visit only
        }
        return out;
      }),
    [persona],
  );
  /** Add a document, or move it; before another card when `before` is given, else at the end. */
  const put = useCallback(
    (id: string, before?: string) =>
      save((ids) => {
        if (id === before) return ids;
        const rest = ids.filter((i) => i !== id);
        const at = before ? rest.indexOf(before) : -1;
        return at < 0 ? [...rest, id] : [...rest.slice(0, at), id, ...rest.slice(at)];
      }),
    [save],
  );
  const remove = useCallback((id: string) => save((ids) => ids.filter((i) => i !== id)), [save]);
  return { ids, put, remove };
}
